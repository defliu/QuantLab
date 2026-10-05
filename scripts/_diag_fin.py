# coding=utf-8
"""诊断：为什么 ocf_to_or 相关性 1.0，但 ocf_to_profit / q_netprofit_margin / dtprofit_to_profit 相关性约 0。

怀疑：n_income / total_profit / income_tax 这几列存在量纲或口径问题。
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


inc, mi = load(os.path.join(D_FIN, 'income.parquet'),
               KEYS + ['revenue', 'total_profit', 'income_tax', 'n_income',
                       'n_income_attr_p', 'report_type'])
cf, mc = load(os.path.join(D_FIN, 'cashflow.parquet'),
              KEYS + ['n_cashflow_act', 'report_type'])
fi, mf = load(os.path.join(D_FIN, 'fina_indicator.parquet'),
              KEYS + ['netprofit_margin', 'dtprofit_to_profit',
                      'ocf_to_profit', 'ocf_to_or', 'gross_margin'])
print('缺列 income=%s cashflow=%s fina=%s' % (mi, mc, mf))

print()
print('=' * 78)
print('1) 各列基本量级（report_type=1）')
print('=' * 78)
i1 = inc[inc['report_type'].astype(str).isin(['1', '1.0'])]
for c in ['revenue', 'total_profit', 'income_tax', 'n_income',
          'n_income_attr_p']:
    if c not in i1.columns:
        continue
    v = num(i1[c])
    d = v.describe()
    print('  %-20s n=%-8d 非空=%-8d min=%-14.2f p50=%-14.2f max=%-16.2f'
          % (c, len(v), int(v.notna().sum()),
             d['min'], d['50%'], d['max']))

c1 = cf[cf['report_type'].astype(str).isin(['1', '1.0'])]
v = num(c1['n_cashflow_act'])
d = v.describe()
print('  %-20s n=%-8d 非空=%-8d min=%-14.2f p50=%-14.2f max=%-16.2f'
      % ('n_cashflow_act', len(v), int(v.notna().sum()),
         d['min'], d['50%'], d['max']))

print()
print('=' * 78)
print('2) D 侧 fina_indicator 目标字段自身量级')
print('=' * 78)
for c in ['netprofit_margin', 'dtprofit_to_profit', 'ocf_to_profit',
          'ocf_to_or']:
    v = num(fi[c])
    d = v.describe()
    print('  %-22s 非空=%-8d min=%-12.4f p50=%-12.4f max=%-14.4f'
          % (c, int(v.notna().sum()), d['min'], d['50%'], d['max']))

print()
print('=' * 78)
print('3) 抽样对照：看 D 原字段与重算值是否同量级')
print('=' * 78)
i1 = i1.drop_duplicates(subset=KEYS, keep='last')
c1 = c1.drop_duplicates(subset=KEYS, keep='last')
fi1 = fi.drop_duplicates(subset=KEYS, keep='last')
m = i1.merge(c1, on=KEYS, how='inner', suffixes=('', '_cf'))
m = m.merge(fi1, on=KEYS, how='inner')
print('对齐后 %d 行' % len(m))

rev = num(m['revenue'])
m['calc_npm'] = num(m['n_income']) / rev.where(rev.abs() > 1e-12)
m['calc_dtp'] = (num(m['income_tax'])
                 / num(m['total_profit']).where(
                     num(m['total_profit']).abs() > 1e-12))
m['calc_ocf2p'] = (num(m['n_cashflow_act'])
                   / num(m['n_income']).where(
                       num(m['n_income']).abs() > 1e-12))
m['calc_ocf2or'] = (num(m['n_cashflow_act'])
                    / rev.where(rev.abs() > 1e-12))

print()
print('=' * 78)
print('3) 脏数据定位：按合理值域裁剪后重算相关性')
print('=' * 78)
# 比率类字段的合理值域（留足冗余）
BOUND = {
    'netprofit_margin': 1000.0,
    'dtprofit_to_profit': 1000.0,
    'ocf_to_profit': 1000.0,
    'ocf_to_or': 1000.0,
}
for col, b in BOUND.items():
    v = num(m[col])
    bad = int(v.notna().sum()) - int(v.abs().le(b).sum())
    print('  %-22s 非空=%-8d 越界(|x|>%.0f)=%-7d 占比=%.3f%%'
          % (col, int(v.notna().sum()), b, bad,
             100.0 * bad / max(int(v.notna().sum()), 1)))

print()
print('  裁剪到合理值域后，重算相关性：')
pairs = [('netprofit_margin', 'calc_npm', 1.0),
         ('dtprofit_to_profit', 'calc_dtp', 1.0),
         ('ocf_to_profit', 'calc_ocf2p', 1.0),
         ('ocf_to_or', 'calc_ocf2or', 1.0)]
for a, b, _ in pairs:
    da, db = num(m[a]), num(m[b])
    ok = da.notna() & db.notna() & da.abs().le(BOUND[a]) \
        & db.abs().le(1e6)
    n = int(ok.sum())
    if n < 100:
        print('    %-22s n=%d 样本不足' % (a, n))
        continue
    x = da[ok].to_numpy()
    y = db[ok].to_numpy()
    cc = float(np.corrcoef(x, y)[0, 1])
    # 比例一致性检验：y 应等于 x/100（Tushare 比率字段以百分数存储）
    with np.errstate(divide='ignore', invalid='ignore'):
        rr = np.abs(x) / np.where(np.abs(y) < 1e-15, np.nan, np.abs(y))
    ratio = float(np.nanmedian(rr)) if np.isfinite(rr).any() else float('nan')
    print('    %-22s n=%-8d corr=%+.6f  |x|/|y| 中位=%.4f'
          % (a, n, cc, ratio))

cols = ['ts_code', 'end_date', 'netprofit_margin', 'calc_npm',
        'ocf_to_profit', 'calc_ocf2p', 'dtprofit_to_profit', 'calc_dtp',
        'ocf_to_or', 'calc_ocf2or']
sel = m[cols].dropna(subset=['netprofit_margin', 'ocf_to_profit',
                             'dtprofit_to_profit', 'ocf_to_or'])
sel = sel[sel['netprofit_margin'].abs().le(1000)
          & sel['ocf_to_profit'].abs().le(1000)
          & sel['dtprofit_to_profit'].abs().le(1000)
          & sel['ocf_to_or'].abs().le(1000)]
print()
print('  裁剪后抽样对照（前 8 行）：')
with pd.option_context('display.width', 220, 'display.max_columns', 30):
    print(sel.head(8).to_string(index=False))

print()
print('=' * 78)
print('4) 分位数对照（裁剪后）')
print('=' * 78)
for a, b, _ in pairs:
    da, db = num(m[a]), num(m[b])
    ok = da.notna() & db.notna() & da.abs().le(BOUND[a]) \
        & db.abs().le(1e6)
    if int(ok.sum()) < 100:
        continue
    print('  %s vs %s  (n=%d)' % (a, b, int(ok.sum())))
    print('     原字段 分位: %s'
          % np.round(da[ok].quantile([0.05, 0.5, 0.95]).to_numpy(), 4))
    print('     重算   分位: %s'
          % np.round(db[ok].quantile([0.05, 0.5, 0.95]).to_numpy(), 4))

print()
print('=' * 78)
print('5) 判定：n_income 到底是哪个口径')
print('=' * 78)
ok = m['n_income'].notna() & m['n_income_attr_p'].notna()
if ok.any():
    a = num(m.loc[ok, 'n_income'])
    b = num(m.loc[ok, 'n_income_attr_p'])
    eq = float((a - b).abs().lt(1e-6).mean())
    cc = float(np.corrcoef(a, b)[0, 1])
    print('  n_income vs n_income_attr_p: corr=%.6f 完全相等占比=%.2f%%'
          % (cc, eq * 100))
    # 哪个更贴近 fina 的 netprofit_margin*revenue
    rev2 = num(m.loc[ok, 'revenue'])
    npm = num(m.loc[ok, 'netprofit_margin'])
    good = npm.notna() & rev2.abs().gt(1e-12)
    if good.any():
        r2 = rev2[good]
        impl_all = (npm[good] * r2)
        c_all = num(m.loc[ok, 'n_income'])[good]
        c_attr = num(m.loc[ok, 'n_income_attr_p'])[good]
        print('  用 n_income        还原: MAE=%.4f 相对误差中位=%.4f%%'
              % (float(np.mean(np.abs(impl_all - c_all))),
                 float(np.median(np.abs(impl_all - c_all)
                                 / np.abs(impl_all).replace(0, np.nan))) * 100))
        print('  用 n_income_attr_p 还原: MAE=%.4f 相对误差中位=%.4f%%'
              % (float(np.mean(np.abs(impl_all - c_attr))),
                 float(np.median(np.abs(impl_all - c_attr)
                                 / np.abs(impl_all).replace(0, np.nan))) * 100))