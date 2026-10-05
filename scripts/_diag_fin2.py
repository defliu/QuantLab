# coding=utf-8
"""反解 dtprofit_to_profit / ocf_to_profit 的真实分子分母口径。

思路：对目标列 y 和候选原始项 x，建立 y * x 的关系。
若 y = x / z（z 为某原始项），则 y * z 应该是常数或落在已知量纲上。
对每个候选分母 z，计算 y*z 的离散度；离散度最小者即真实口径。
只读，不写生产文件。
"""
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

D_FIN = r'D:\astock\finance'
KEYS = ['ts_code', 'end_date', 'ann_date']


def num(a):
    return pd.to_numeric(a, errors='coerce').astype('float64')


def load(path, cols):
    pf = pq.ParquetFile(path)
    use = [c for c in cols if c in pf.schema_arrow.names]
    df = pf.read(columns=use).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    return df, sorted(set(cols) - set(pf.schema_arrow.names))


inc, _ = load(os.path.join(D_FIN, 'income.parquet'),
              KEYS + ['report_type', 'revenue', 'total_profit', 'income_tax',
                      'n_income', 'n_income_attr_p', 'operate_profit'])
cf, _ = load(os.path.join(D_FIN, 'cashflow.parquet'),
             KEYS + ['report_type', 'n_cashflow_act'])
bs, _ = load(os.path.join(D_FIN, 'balancesheet.parquet'),
             KEYS + ['report_type'])
fi, _ = load(os.path.join(D_FIN, 'fina_indicator.parquet'),
             KEYS + ['dtprofit_to_profit', 'ocf_to_profit',
                     'ocf_to_or', 'netprofit_margin'])
print('income 全列数=%d' % inc.shape[1])

inc = inc[inc['report_type'].astype(str).isin(['1', '1.0'])]
cf = cf[cf['report_type'].astype(str).isin(['1', '1.0'])]

# 更宽的候选分母集合
CAND = ['n_income', 'n_income_attr_p', 'total_profit', 'revenue',
        'operate_profit', 'n_cashflow_act', 'income_tax']

# income 全列都拉进来，找同族候选
pf = pq.ParquetFile(os.path.join(D_FIN, 'income.parquet'))
all_inc = [c for c in pf.schema_arrow.names]
print()
print('income 全部列名（找净利润/利润总额同族）：')
print([c for c in all_inc if any(
    k in c for k in ('profit', 'income', 'tax', 'revenue', 'cost'))])

full, _ = load(os.path.join(D_FIN, 'income.parquet'),
               KEYS + ['report_type'] + all_inc)
full = full[full['report_type'].astype(str).isin(['1', '1.0'])]
full = full.drop_duplicates(subset=KEYS, keep='last')
cf = cf.drop_duplicates(subset=KEYS, keep='last')
fi = fi.drop_duplicates(subset=KEYS, keep='last')

m = full.merge(cf, on=KEYS, how='inner', suffixes=('', '_cf'))
m = m.merge(fi, on=KEYS, how='inner')
print('对齐后 %d 行' % len(m))

# 只保留 y 在合理值域的样本，避免脏值干扰反解
for target in ['dtprofit_to_profit', 'ocf_to_profit']:
    y = num(m[target])
    base = y.notna() & y.abs().le(1000)
    print()
    print('=' * 78)
    print('反解 %s（有效样本 %d）' % (target, int(base.sum())))
    print('=' * 78)
    scored = []
    for c in CAND:
        if c not in m.columns:
            continue
        x = num(m[c])
        prod = (y * x)
        v = prod[base & x.notna()]
        if len(v) < 1000:
            continue
        # 离散度：变异系数，越小说明 y*x 越接近常数
        cv = float(v.std() / abs(v.mean())) if v.mean() != 0 else float('inf')
        scored.append((cv, c, float(v.median()), len(v)))
    scored.sort()
    print('  假设 y = 分子/该列，则 y*该列 应≈分子（离散度越小越像）：')
    for cv, c, med, n in scored[:6]:
        print('    %-22s CV=%-10.4f 中位=%-18.2f n=%d' % (c, cv, med, n))

    # 直接反解：y * x 落在哪个原始项量级上
    print()
    print('  逐列比对 y*该列 与 income 中各原始项（相关性）：')
    best = None
    for c in CAND:
        if c not in m.columns:
            continue
        x = num(m[c])
        ok = base & x.notna()
        if int(ok.sum()) < 1000:
            continue
        prod = y[ok] * x[ok]
        for c2 in CAND:
            if c2 == c or c2 not in m.columns:
                continue
            z = num(m[c2])
            ok2 = ok & z.notna()
            if int(ok2.sum()) < 1000:
                continue
            a = prod[ok2].to_numpy()
            b = z[ok2].to_numpy()
            cc = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else 0
            if best is None or abs(cc) > abs(best[0]):
                best = (cc, c, c2, len(a))
    if best:
        print('    最强：y * %s  ≈  %s   corr=%.6f  n=%d'
              % (best[1], best[2], best[0], best[3]))

print()
print('=' * 78)
print('交叉验证：ocf_to_profit * X 是不是 n_cashflow_act')
print('=' * 78)
y = num(m['ocf_to_profit'])
ocf = num(m['n_cashflow_act'])
for c in ['n_income', 'n_income_attr_p']:
    if c not in m.columns:
        continue
    z = num(m[c])
    ok = y.notna() & z.notna() & y.abs().le(1000) & z.notna()
    if int(ok.sum()) < 100:
        continue
    implied = (y[ok] * z[ok])          # 若 y = ocf/z 则 implied 应 = ocf
    cc = float(np.corrcoef(implied, ocf[ok])[0, 1])
    ratio = float(np.median(np.abs(implied.to_numpy())
                            / np.abs(ocf[ok].to_numpy())))
    print('  ocf_to_profit * %-20s vs n_cashflow_act: corr=%.6f 比值中位=%.4f'
          % (c, cc, ratio))

print()
print('=' * 78)
print('交叉验证：dtprofit_to_profit * X 是不是 income_tax')
print('=' * 78)
y = num(m['dtprofit_to_profit'])
tax = num(m['income_tax'])
for c in ['total_profit', 'operate_profit', 'n_income', 'n_income_attr_p']:
    if c not in m.columns:
        continue
    z = num(m[c])
    ok = y.notna() & z.notna() & y.abs().le(1000)
    if int(ok.sum()) < 100:
        continue
    implied = (y[ok] * z[ok])
    a = implied.to_numpy()
    b = tax[ok].to_numpy()
    msk = np.isfinite(a) & np.isfinite(b)
    if msk.sum() < 100:
        continue
    cc = float(np.corrcoef(a[msk], b[msk])[0, 1])
    ratio = float(np.median(np.abs(a[msk])
                            / np.where(np.abs(b[msk]) < 1e-15, np.nan,
                                       np.abs(b[msk]))))
    print('  dtprofit_to_profit * %-16s vs income_tax: corr=%.6f 比值中位=%.4f'
          % (c, cc, ratio))