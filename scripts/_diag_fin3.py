# coding=utf-8
"""矩阵化全组合反解 fina_indicator 衍生字段口径（表限定列名版）。

相对 _diag_fin2.py 的四处关键改进：
1. 候选池从 7 列扩到 income + cashflow + balancesheet + fina_indicator
   四表全部数值列。上一轮漏掉 profit_dedt / q_dtprofit / q_opincome 等
   同表列，导致 dtprofit_to_profit 找不到分子分母。
2. 候选列一律带表前缀（income.n_income / fina_indicator.profit_dedt），
   杜绝跨表同名列被压成一个列名后取错来源表。
3. 先对键做下采样、再按表 merge 并立刻抽出 float32 向量，
   不保留 4 份全量 joined frame（省内存，且列与 m 行天然对齐）。
4. 硬确认阶段按 (表, 列) 精确重读 float64 原始值，
   不依赖 float32 矩阵，避免精度吃掉误差。

数学前提：若 y = C * a / b（a、b 为候选池原始项，C 为 1 或 100），
则 y * b == C * a，等价于 corr(y * b, a) == 1 且比值稳定在 C。

单季度 q_* 字段走累计差分，不在本脚本范围（见 _audit_batch2_calc.py）。
只读，不写生产文件。
"""
import gc
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

D_FIN = r'D:\astock\finance'
KEYS = ['ts_code', 'end_date', 'ann_date']
TABLES = ['income', 'cashflow', 'balancesheet', 'fina_indicator']

TARGETS = [
    'netprofit_margin',
    'dtprofit_to_profit',
    'ocf_to_profit',
    'ocf_to_or',
    'profit_to_gr',
    'grossprofit_margin',
    'tax_to_ebt',
]

# 与目标同源、作为分子分母会造出自乘伪相关的列（按 basename 排除）
SELF_FAMILY = {
    'netprofit_margin': ['q_netprofit_margin'],
    'dtprofit_to_profit': ['q_dtprofit_to_profit'],
    'ocf_to_or': ['q_ocf_to_or'],
    'profit_to_gr': ['profit_to_op'],
    'grossprofit_margin': [],
    'tax_to_ebt': [],
    'ocf_to_profit': [],
}

BOUND = 1000.0
BLOCK = 20
MAX_ROWS = 150000
MIN_CORR = 0.90
MIN_N = 5000


def num(a):
    return pd.to_numeric(a, errors='coerce').astype('float32')


def has_report_type(path):
    return 'report_type' in set(pq.ParquetFile(path).schema_arrow.names)


def read_keys(path):
    """fina_indicator 无 report_type 列，其 LOGIC KEY 本就是 (ts_code,end_date)。"""
    df = pq.ParquetFile(path).read(
        columns=KEYS + (['report_type'] if has_report_type(path) else [])
    ).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    if 'report_type' in df.columns:
        df = df[df['report_type'].astype(str).isin(['1', '1.0'])]
    return df.drop_duplicates(subset=KEYS, keep='last')[KEYS]


print('=' * 92)
print('第 1 步：四表键对齐 + 目标列')
print('=' * 92)
m = read_keys(os.path.join(D_FIN, 'income.parquet'))
print('  income        -> %d 行' % len(m))
for nm in ['cashflow', 'balancesheet', 'fina_indicator']:
    k = read_keys(os.path.join(D_FIN, nm + '.parquet'))
    m = m.merge(k, on=KEYS, how='inner')
    print('  join %-13s -> %d 行' % (nm, len(m)))

pf_fi = pq.ParquetFile(os.path.join(D_FIN, 'fina_indicator.parquet'))
fi_names = list(pf_fi.schema_arrow.names)
tgt = [c for c in TARGETS if c in fi_names]
print('  目标命中 %d/%d -> %s' % (len(tgt), len(TARGETS), tgt))
Y = pf_fi.read(columns=KEYS + tgt).to_pandas()
if isinstance(Y.index, pd.MultiIndex):
    Y = Y.reset_index()
Y = Y.drop_duplicates(subset=KEYS, keep='last')
m = m.merge(Y, on=KEYS, how='inner')
print('  含目标列后 %d 行' % len(m))

if len(m) > MAX_ROWS:
    step = int(np.ceil(len(m) / float(MAX_ROWS)))
    m = m.iloc[::step].reset_index(drop=True)
    print('  下采样 stride=%d -> %d 行' % (step, len(m)))

print()
print('=' * 92)
print('第 2 步：候选池（表限定列名，抽出 float32 向量）')
print('=' * 92)
pool = []
colvec = []
for t in TABLES:
    p = os.path.join(D_FIN, t + '.parquet')
    all_names = list(pq.ParquetFile(p).schema_arrow.names)
    names = [c for c in all_names
             if c not in KEYS and c != 'report_type'
             and not c.startswith('__index_level_')]
    df = pq.ParquetFile(p).read(columns=KEYS + names).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    if has_report_type(p):
        rt = pq.ParquetFile(p).read(columns=KEYS + ['report_type']).to_pandas()
        if isinstance(rt.index, pd.MultiIndex):
            rt = rt.reset_index()
        rt = rt[rt['report_type'].astype(str).isin(['1', '1.0'])][KEYS]
        df = df.merge(rt, on=KEYS, how='inner')
    df = df.drop_duplicates(subset=KEYS, keep='last')

    j = m[KEYS].merge(df, on=KEYS, how='inner')
    if len(j) != len(m):
        print('  !! %s join 掉行 %d，列向量会错位' % (t, len(m) - len(j)))
    for c in names:
        pool.append('%s.%s' % (t, c))
        colvec.append(num(j[c]).to_numpy())
    print('  %-13s 候选 %3d 列  join 后 %d 行' % (t, len(names), len(j)))
    del df, j
    gc.collect()

M = np.column_stack(colvec) if colvec else np.empty((len(m), 0), 'float32')
del colvec
gc.collect()

keep = np.isfinite(M).any(axis=0)
M = M[:, keep]
pool = [c for c, o in zip(pool, keep) if o]
print('  候选池 %d 列（全空已剔除）  矩阵 %s'
      % (len(pool), 'x'.join(str(x) for x in M.shape)))


def zscore(a):
    mu = np.nanmean(a, axis=0)
    sd = np.nanstd(a, axis=0)
    sd = np.where(sd < 1e-12, np.nan, sd).astype('float32')
    return ((a - mu) / sd).astype('float32')


def load_f64(qual, base_keys):
    """按 (表, 列) 精确重读 float64，与 base_keys 行序严格对齐。"""
    tt, cc = qual.split('.', 1)
    p = os.path.join(D_FIN, tt + '.parquet')
    df = pq.ParquetFile(p).read(columns=KEYS + [cc]).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    if has_report_type(p):
        rt = pq.ParquetFile(p).read(columns=KEYS + ['report_type']).to_pandas()
        if isinstance(rt.index, pd.MultiIndex):
            rt = rt.reset_index()
        rt = rt[rt['report_type'].astype(str).isin(['1', '1.0'])][KEYS]
        df = df.merge(rt, on=KEYS, how='inner')
    df = df.drop_duplicates(subset=KEYS, keep='last')
    out = base_keys.merge(df, on=KEYS, how='left')
    return pd.to_numeric(out[cc], errors='coerce').astype('float64').to_numpy()


for t in tgt:
    y_all = num(m[t]).to_numpy()
    base = np.isfinite(y_all) & (np.abs(y_all) <= BOUND)
    nb = int(base.sum())
    print()
    print('=' * 92)
    print('%s   有效样本 %d / %d' % (t, nb, len(m)))
    print('=' * 92)
    if nb < MIN_N:
        print('  样本不足 %d，跳过' % MIN_N)
        continue

    Mx = M[base, :]
    yv = y_all[base].astype('float32')
    valid = np.isfinite(Mx).all(axis=0)
    if not valid.any():
        print('  无全非空原始项')
        continue
    cols = [pool[i] for i in np.where(valid)[0]]
    Mx = Mx[:, valid]
    n = float(len(yv))

    excl = set([t]) | set(SELF_FAMILY.get(t, []))
    hits = []
    for s in range(0, len(cols), BLOCK):
        e = min(s + BLOCK, len(cols))
        block_cols = cols[s:e]
        good = [i for i, c in enumerate(block_cols)
                if c.split('.', 1)[1] not in excl]
        if not good:
            continue
        sub = Mx[:, s:e]
        Xb = zscore(sub)
        Mh = zscore(yv[:, None] * sub)
        # Xb/Mh 均已 z-score，内积 / n 即相关系数
        C = (Mh[:, good].T @ Xb) / n
        for gi, i in enumerate(good):
            row = C[gi]
            for k in np.argsort(-np.abs(row))[:3]:
                nc = cols[int(k)]
                if nc == block_cols[i] or nc.split('.', 1)[1] in excl:
                    continue
                v = float(row[k])
                if abs(v) < MIN_CORR:
                    continue
                hits.append((abs(v), block_cols[i], nc, v))
        del sub, Xb, Mh, C
    hits.sort(reverse=True)
    del Mx
    gc.collect()

    if not hits:
        print('  未找到 |corr|>=%.2f 的组合' % MIN_CORR)
        continue

    base_keys = m.loc[base, KEYS]
    yd = pd.to_numeric(m.loc[base, t], errors='coerce').astype('float64').to_numpy()

    need = set()
    for a, dcol, ncol, v in hits[:24]:
        need.add(dcol)
        need.add(ncol)
    f64 = {}
    for q in sorted(need):
        f64[q] = load_f64(q, base_keys)
    print('  已重读 %d 个 float64 原始列供硬确认' % len(f64))

    seen = set()
    shown = 0
    for a, dcol, ncol, v in hits:
        if (dcol, ncol) in seen:
            continue
        seen.add((dcol, ncol))
        den = f64[dcol]
        numr = f64[ncol]
        prod = yd * den
        msk = np.isfinite(prod) & np.isfinite(numr) & (np.abs(numr) > 1e-9)
        if msk.sum() < MIN_N:
            continue
        rel = np.abs(prod[msk]) / np.abs(numr[msk])
        ratio = float(np.nanmedian(rel))
        hit1 = float(np.nanmean(rel < 0.001))
        hit5 = float(np.nanmean(rel < 0.005))
        scale = float(np.nanmedian(np.abs(numr[msk])))
        mae = float(np.nanmedian(np.abs(prod[msk] - ratio * numr[msk])))
        tag = ''
        if ratio > 0.5 and ratio < 2.0:
            tag = ' [C=1]'
        elif ratio > 50 and ratio < 200:
            tag = ' [C=100]'
        print('  y * %-32s ~ %-32s corr=%+.6f%s'
              % (dcol, ncol, v, tag))
        print('      中位比例=%12.4f  |rel|<0.1%%=%.4f  |rel|<0.5%%=%.4f'
              % (ratio, hit1, hit5))
        print('      中位绝对误差=%.6g  分子中位量级=%.6g  相对误差=%.3e  n=%d'
              % (mae, scale, mae / scale if scale > 0 else float('nan'),
                 int(msk.sum())))
        shown += 1
        if shown >= 6:
            break
    del f64
    gc.collect()
