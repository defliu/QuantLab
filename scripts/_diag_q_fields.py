# coding=utf-8
"""反解 fina_indicator 的 q_* 单季度字段口径。

相对初版修掉的四个结构性缺陷（初版会产出看似专业的错误结论）：

1. 行错位：初版用各表自身行序建候选矩阵 sq[k]，却与 inner-join 后的 m
   比较长度，两者行数不同，M[base,:] 要么崩要么静默取错股票。
   -> 本版先建 m，再把每张表的列 merge 到 m[KEYS2] 上，天然对齐。

2. 差分基准错：初版 load_table 只 drop_duplicates 没 sort_values，
   而 single_quote 用 groupby(...).shift(1) 取「物理上一行」。
   parquet 行序不保证按 end_date 时，Q2 会减到非 Q1。
   -> 本版对每张表显式 sort_values(['ts_code','end_date']) 后再 shift。
   （已探测：四表当前确实已升序，但这是依赖不能是假设。）

3. 多公告版本污染差分：同一 (ts_code,end_date) 因 ann_date 不同有多版本时，
   shift(1) 会减到同季度的另一个公告版本，而非上一季度。
   -> 本版先按 (ts_code,end_date) 取最新 ann_date 去重到唯一季度，
   再排序差分。

4. 差分前下采样：初版对 m 下采样后才对列做差分（若照搬会错），
   本版差分始终在全表完成，再 join 到下采样后的 m。

另外两处口径决定：
- 连接键用 (ts_code,end_date) 而非含 ann_date：三表对同一季度的 ann_date
  常差 1-2 天，按全 KEYS inner join 会误丢样本。本版以 fina_indicator 的
  ann_date 为准（PIT 可见性属另一独立审计项）。
- 候选池区分存量/流量：balancesheet 是时点存量，不做差分；
  income/cashflow 是累计流量，做单季度化。

数学前提：y = C * a / b（C 取 1 或 100）=> y * b == C * a，
即 corr(y*b, a) == 1 且比值稳定落在 C。

只读，不写生产文件。
"""
import gc
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

D_FIN = r'D:\astock\finance'
KEYS = ['ts_code', 'end_date', 'ann_date']
K2 = ['ts_code', 'end_date']
MAX_ROWS = 120000
MIN_N = 5000
MIN_CORR = 0.90
BOUND = 1000.0
BLOCK = 25

RATIO_TARGETS = [
    'q_roe',
    'q_gsprofit_margin',
    'q_netprofit_margin',
    'q_dtprofit_to_profit',
    'q_ocf_to_sales',
    'q_ocf_to_or',
]

QOQ_TARGETS = ['q_sales_qoq', 'q_netprofit_qoq']

# 比率类列名族：拿它们当分子/分母会造出自乘伪相关
RATIO_LIKE = ('margin', 'ratio', 'turn', 'days', '_pct', 'qoq', 'yoy',
              'roe', 'rate', 'cover')

FLOW_TABLES = ['income', 'cashflow']
STOCK_TABLES = ['balancesheet']
META_TABLES = ['fina_indicator']

_cache = {}


def is_num(tp):
    return (pa.types.is_integer(tp) or pa.types.is_floating(tp)
            or pa.types.is_decimal(tp))


def num(a):
    return pd.to_numeric(a, errors='coerce').astype('float32')


def read_table(t, columns=None):
    """读表 -> 过滤 report_type=1 -> 按季度去重到唯一行 -> 按 (ts_code,end_date) 升序。"""
    ck = (t, tuple(columns) if columns else None)
    if ck in _cache:
        return _cache[ck]
    p = os.path.join(D_FIN, t + '.parquet')
    schema = pq.ParquetFile(p).schema_arrow
    all_names = list(schema.names)
    skip = set(KEYS) | {'report_type'}
    if columns is None:
        cols = [c for c in all_names
                if c not in skip and not c.startswith('__index_level_')
                and is_num(schema.field(c).type)]
    else:
        cols = [c for c in columns if c in all_names]
    df = pq.ParquetFile(p).read(columns=KEYS + cols).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    if 'report_type' in all_names:
        rt = pq.ParquetFile(p).read(columns=KEYS + ['report_type']).to_pandas()
        if isinstance(rt.index, pd.MultiIndex):
            rt = rt.reset_index()
        keep_rt = rt[rt['report_type'].astype(str).isin(['1', '1.0'])][KEYS]
        df = df.merge(keep_rt, on=KEYS, how='inner')
    # 缺陷 3：同季度多公告版本 -> 只留最新 ann_date，保证 K2 唯一
    df = (df.sort_values('ann_date', kind='stable')
            .drop_duplicates(subset=K2, keep='last'))
    # 缺陷 2：差分前必须显式排序
    df = (df.assign(_ed=pd.to_datetime(df['end_date'], format='mixed',
                                       errors='coerce'))
            .sort_values(['ts_code', '_ed'], kind='stable')
            .reset_index(drop=True))
    _cache[ck] = df
    return df


def quarter_tag(ed):
    return (ed.dt.month - 1) // 3 + 1


def to_single_quarter(df, col):
    """累计转单季：Q1 取累计；Q2-Q4 = 本期累计 - 同年上期累计。

    必须在唯一季度、已排序的帧上调用。
    """
    c = num(df[col])
    prev = c.groupby([df['ts_code'], df['_ed'].dt.year], sort=False).shift(1)
    out = c - prev
    return out.mask(quarter_tag(df['_ed']) == 1, c)


print('=' * 96)
print('第 1 步：唯一季度键对齐')
print('=' * 96)
key_frames = {}
for t in FLOW_TABLES + STOCK_TABLES + META_TABLES:
    key_frames[t] = read_table(t)[KEYS]
    print('  %-15s 唯一季度行 %d' % (t, len(key_frames[t])))

m = key_frames['income'][K2]
for t in ['cashflow', 'fina_indicator']:
    m = m.merge(key_frames[t][K2], on=K2, how='inner')
    print('  join %-15s -> %d 行' % (t, len(m)))

fi = read_table('fina_indicator')
tg = [c for c in RATIO_TARGETS + QOQ_TARGETS if c in fi.columns]
print('  q_ 目标命中 %d/%d -> %s'
      % (len(tg), len(RATIO_TARGETS) + len(QOQ_TARGETS), tg))
Y = fi[K2 + tg].copy()
m = m.merge(Y, on=K2, how='inner')
m['ann_date'] = fi[K2 + ['ann_date']].merge(m[K2], on=K2, how='left')['ann_date']
m = m.reset_index(drop=True)
print('  目标非全空后 %d 行' % len(m))

if len(m) > MAX_ROWS:
    step = int(np.ceil(len(m) / float(MAX_ROWS)))
    m = m.iloc[::step].reset_index(drop=True)
    print('  下采样 stride=%d -> %d 行（差分已在全表完成，此处仅压缩比对）'
          % (step, len(m)))

print()
print('=' * 96)
print('第 2 步：候选池（全表单季度化 -> join 到 m，对齐后取 float32）')
print('=' * 96)
pool = []
colvec = []
for t in FLOW_TABLES:
    df = read_table(t)
    vals = [c for c in df.columns
            if c not in set(KEYS) | set(K2) | {'_ed', 'report_type'}
            and not c.startswith('__index_level_')]
    sq = df[K2].copy()
    for c in vals:
        sq[c] = to_single_quarter(df, c).to_numpy()
    j = m[K2].merge(sq, on=K2, how='left')
    if len(j) != len(m):
        raise SystemExit('致命：%s join 后行数 %d != m %d，列向量会错位'
                         % (t, len(j), len(m)))
    for c in vals:
        pool.append('%s.sq_%s' % (t, c))
        colvec.append(num(j[c]).to_numpy())
    print('  %-15s 单季列 %3d  join %d 行' % (t, len(vals), len(j)))
    del df, sq, j
    gc.collect()

for t in STOCK_TABLES:
    df = read_table(t)
    vals = [c for c in df.columns
            if c not in set(KEYS) | set(K2) | {'_ed', 'report_type'}
            and not c.startswith('__index_level_')]
    j = m[K2].merge(df[K2 + vals], on=K2, how='left')
    if len(j) != len(m):
        raise SystemExit('致命：%s join 后行数 %d != m %d' % (t, len(j), len(m)))
    for c in vals:
        pool.append('%s.%s' % (t, c))
        colvec.append(num(j[c]).to_numpy())
    print('  %-15s 存量列 %3d  join %d 行（不差分）' % (t, len(vals), len(j)))
    del df, j
    gc.collect()

df = read_table('fina_indicator')
vals = [c for c in df.columns
        if c not in set(KEYS) | set(K2) | {'_ed', 'report_type'}
        and not c.startswith('__index_level_')]
# 排除比率类列与所有 q_ 列（自乘伪相关 + 已是目标）
vals = [c for c in vals
        if not c.startswith('q_')
        and not any(k in c for k in RATIO_LIKE)]
j = m[K2].merge(df[K2 + vals], on=K2, how='left')
if len(j) != len(m):
    raise SystemExit('致命：fina_indicator join 后行数 %d != m %d' % (len(j), len(m)))
for c in vals:
    pool.append('fina_indicator.%s' % c)
    colvec.append(num(j[c]).to_numpy())
print('  %-15s 辅助列 %3d  join %d 行' % ('fina_indicator', len(vals), len(j)))
del df, j
gc.collect()

M = np.column_stack(colvec) if colvec else np.empty((len(m), 0), 'float32')
del colvec
gc.collect()
alive = np.isfinite(M).any(axis=0)
M = M[:, alive]
pool = [c for c, o in zip(pool, alive) if o]
print('  候选池 %d 列  矩阵 %s' % (len(pool), 'x'.join(map(str, M.shape))))


def corr_pair(u, v, mask):
    a = u[mask].astype('float64')
    b = v[mask].astype('float64')
    if a.size < MIN_N:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    da = a.std()
    db = b.std()
    if da < 1e-12 or db < 1e-12:
        return 0.0
    return float((a * b).mean() / (da * db))


def load_f64(qual, base_keys):
    """按候选列语义重算 float64：flow 列单季度化，stock 列取原值。"""
    t, rest = qual.split('.', 1)
    is_sq = rest.startswith('sq_')
    col = rest[3:] if is_sq else rest
    df = read_table(t)
    src = (to_single_quarter(df, col) if is_sq else num(df[col]))
    right = df[K2].copy()
    right['_v'] = src.to_numpy()
    j = base_keys[K2].merge(right, on=K2, how='left')
    return pd.to_numeric(j['_v'], errors='coerce').astype('float64').to_numpy()


print()
print('=' * 96)
print('第 3 步：单季比率型 q_* 反解  y = C * a / b')
print('=' * 96)
verdict = {}
for t in RATIO_TARGETS:
    if t not in m.columns:
        print('\n[%s] 不在 fina_indicator 中，跳过' % t)
        continue
    y_all = num(m[t]).to_numpy()
    base = np.isfinite(y_all) & (np.abs(y_all) <= BOUND)
    nb = int(base.sum())
    print('\n' + '-' * 96)
    print('[单季比率] %-20s 有效样本 %d / %d' % (t, nb, len(m)))
    if nb < MIN_N:
        print('  样本不足 %d，跳过' % MIN_N)
        continue

    Mx = M[base, :]
    yv = y_all[base]
    fin = np.isfinite(Mx)
    yfin = np.isfinite(yv)
    hits = []
    for s in range(0, len(pool), BLOCK):
        e = min(s + BLOCK, len(pool))
        sub = Mx[:, s:e]
        finb = fin[:, s:e] & yfin[:, None]
        lhs = yv[:, None] * sub
        finb = finb & np.isfinite(lhs)
        for i in range(e - s):
            gi = s + i
            if finb[:, i].sum() < MIN_N:
                continue
            for k in range(e - s):
                gk = s + k
                if gk == gi:
                    continue
                msk = finb[:, i] & finb[:, k]
                if msk.sum() < MIN_N:
                    continue
                v = corr_pair(lhs[:, i], sub[:, k], msk)
                if abs(v) >= MIN_CORR:
                    hits.append((abs(v), pool[gk], pool[gi], v))
        del sub, lhs, finb
    hits.sort(reverse=True)
    del Mx
    gc.collect()
    if not hits:
        print('  未找到 |corr|>=%.2f 的组合' % MIN_CORR)
        continue

    base_keys = m.loc[base].reset_index(drop=True)
    yd = pd.to_numeric(m.loc[base, t], errors='coerce').astype('float64').to_numpy()
    need = set()
    for a, dn, nn, v in hits[:30]:
        need.add(dn)
        need.add(nn)
    f64 = {q: load_f64(q, base_keys) for q in sorted(need)}
    print('  重读 %d 个 float64 原始列供硬确认' % len(f64))

    seen = set()
    shown = 0
    for a, dn, nn, v in hits:
        if (dn, nn) in seen:
            continue
        seen.add((dn, nn))
        lhs = yd * f64[dn]
        rhs = f64[nn]
        msk = np.isfinite(lhs) & np.isfinite(rhs) & (np.abs(rhs) > 1e-9)
        if msk.sum() < MIN_N:
            continue
        rel = np.abs(lhs[msk]) / np.abs(rhs[msk])
        ratio = float(np.nanmedian(rel))
        pq = np.nanpercentile(rel, [50, 90, 99])
        h1 = float(np.mean(rel < 0.01))
        sgn = float(np.mean(np.sign(lhs[msk]) == np.sign(rhs[msk])))
        tag = ' [C=1]' if 0.5 < ratio < 2 else (
            ' [C=100]' if 50 < ratio < 200 else '')
        print('  y * %-28s ~ %-28s corr=%+.6f%s' % (dn, nn, v, tag))
        print('      中位比例=%11.4f  p50=%.2e p90=%.2e p99=%.2e  '
              '<1%%=%.4f  符号=%.4f  n=%d'
              % (ratio, pq[0], pq[1], pq[2], h1, sgn, int(msk.sum())))
        verdict[t] = (dn, nn, ratio, pq[0], h1, sgn, int(msk.sum()))
        shown += 1
        if shown >= 5:
            break
    del f64
    gc.collect()

print()
print('=' * 96)
print('第 4 步：qoq 型反解  y = (cur - prev) / |prev| * C')
print('=' * 96)
inc = read_table('income')
cf = read_table('cashflow')
QQ_BASE = {}
for src, tname in [(inc, 'income'), (cf, 'cashflow')]:
    for c in ['revenue', 'total_revenue', 'operate_profit', 'n_income',
              'n_income_attr_p']:
        if c in src.columns:
            s = to_single_quarter(src, c)
            QQ_BASE['%s.sq_%s' % (tname, c)] = (s, src['ts_code'], src[K2])
del inc, cf
gc.collect()

for t in QOQ_TARGETS:
    if t not in m.columns:
        print('\n[%s] 不在 fina_indicator 中，跳过' % t)
        continue
    y_all = num(m[t]).to_numpy()
    base = np.isfinite(y_all) & (np.abs(y_all) <= BOUND)
    print('\n' + '-' * 96)
    print('[环比] %-20s 有效样本 %d / %d' % (t, int(base.sum()), len(m)))
    if base.sum() < MIN_N:
        print('  样本不足，跳过')
        continue
    bk = m.loc[base].reset_index(drop=True)
    yd = pd.to_numeric(m.loc[base, t], errors='coerce').astype('float64').to_numpy()
    rows = []
    for qual, (s, code, kf) in sorted(QQ_BASE.items()):
        prev = s.groupby(code, sort=False).shift(1)
        prevf = prev.to_numpy().astype('float64')
        cur = s.to_numpy().astype('float64')
        den = np.abs(prevf)
        gr = (cur - prevf) / np.where(den > 1e-9, den, np.nan)
        right = kf.copy()
        right['_v'] = gr
        j = bk[K2].merge(right, on=K2, how='left')
        g = j['_v'].to_numpy()
        msk = np.isfinite(g) & np.isfinite(yd)
        if msk.sum() < MIN_N:
            continue
        rel = np.abs(yd[msk]) / np.abs(g[msk])
        ratio = float(np.nanmedian(rel))
        p50 = float(np.nanpercentile(rel, 50))
        h1 = float(np.mean(rel < 0.01))
        rows.append((h1, qual, ratio, p50, int(msk.sum())))
    rows.sort(reverse=True)
    for h1, qual, ratio, p50, n in rows[:5]:
        tag = ' [C=1]' if 0.5 < ratio < 2 else (
            ' [C=100]' if 50 < ratio < 200 else '')
        print('  y ~ (%s)  中位比例=%.4f%s  p50=%.2e  <1%%=%.4f  n=%d'
              % (qual, ratio, tag, p50, h1, n))
        if tag:
            verdict[t] = (qual, '(cur-prev)/|prev|', ratio, p50, h1,
                          float('nan'), n)
    if not rows:
        print('  无候选通过样本门槛')

print()
print('=' * 96)
print('汇总（只列 C 干净落位者）')
print('=' * 96)
for t, v in sorted(verdict.items()):
    print('  %-20s 分子=%-26s 分母=%-26s C=%.4f  p50=%.2e  <1%%=%.4f  n=%d'
          % (t, v[1], v[0], v[2], v[3], v[4], v[6]))