# coding=utf-8
"""对 _diag_fin3.py 反解出的 7 个候选公式做严格硬确认。

_diag_fin3 给出「中位比例精确=100」但 |rel|<0.1% 命中率≈0，说明误差重尾：
多数点极准、少数点严重偏离。中位数不能单独作为公式成立的证据，
本脚本补齐四类硬指标：

1. rel 的分位数分布（p50/p75/p90/p95/p99）+ 逐级命中率（<0.1%/<1%/<5%/<20%）
2. 符号一致性（y*b 与 a 是否恒同号，排除「比例靠绝对值凑出来」的伪相关）
3. 分年度稳定性（公式是否只在部分年份成立）
4. 排除 comp_type 这类分类编码字段造成的伪命中（corr=±1 但量纲毫无意义）

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

# (目标字段, 分子限定列, 分母限定列, 期望比例 C)
FORMULAS = [
    ('netprofit_margin',   'income.n_income',            'income.revenue',             100.0),
    ('dtprofit_to_profit', 'fina_indicator.profit_dedt', 'income.n_income_attr_p',     100.0),
    ('ocf_to_profit',      'cashflow.n_cashflow_act',    'income.operate_profit',      100.0),
    ('ocf_to_or',          'cashflow.n_cashflow_act',    'income.revenue',               1.0),
    ('profit_to_gr',       'income.n_income',            'income.total_revenue',       100.0),
    ('grossprofit_margin', 'fina_indicator.gross_margin', 'income.revenue',            100.0),
    ('tax_to_ebt',         'income.income_tax',          'income.total_profit',        100.0),
]

BOUND = 1000.0
MIN_N = 5000


def num(a):
    return pd.to_numeric(a, errors='coerce')


def has_report_type(path):
    return 'report_type' in set(pq.ParquetFile(path).schema_arrow.names)


_cache = {}


def load_table(t):
    if t in _cache:
        return _cache[t]
    p = os.path.join(D_FIN, t + '.parquet')
    df = pq.ParquetFile(p).read().to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    if has_report_type(p):
        df = df[df['report_type'].astype(str).isin(['1', '1.0'])]
    df = df.drop_duplicates(subset=KEYS, keep='last')
    _cache[t] = df
    return df


def col(qual, keys):
    t, c = qual.split('.', 1)
    j = keys.merge(load_table(t)[KEYS + [c]], on=KEYS, how='left')
    return num(j[c]).to_numpy(dtype='float64')


# ---- 组装对齐帧 ----
print('=' * 96)
print('对齐帧组装')
print('=' * 96)
need_tables = sorted({'fina_indicator', 'income', 'cashflow'}
                     for _ in ()).__class__() if False else None
tbls = set()
for _, nq, dq, _ in FORMULAS:
    tbls.add(nq.split('.', 1)[0])
    tbls.add(dq.split('.', 1)[0])
tbls.add('fina_indicator')
print('  涉及表: %s' % sorted(tbls))

m = load_table('income')[KEYS]
for t in sorted(tbls):
    m = m.merge(load_table(t)[KEYS], on=KEYS, how='inner')
    print('  join %-13s -> %d 行' % (t, len(m)))

tgts = sorted({f[0] for f in FORMULAS})
fi = load_table('fina_indicator')[KEYS + [c for c in tgts if c in load_table('fina_indicator').columns]]
m = m.merge(fi.drop_duplicates(subset=KEYS), on=KEYS, how='inner')
print('  含目标列后 %d 行' % len(m))

# ---- 逐公式硬确认 ----
summary = []
for tgt, nq, dq, C in FORMULAS:
    print()
    print('=' * 96)
    print('%s  =  %s / %s  x %g' % (tgt, nq, dq, C))
    print('=' * 96)
    y = num(m[tgt]).to_numpy(dtype='float64')
    a = col(nq, m[KEYS])
    b = col(dq, m[KEYS])

    msk = (np.isfinite(y) & np.isfinite(a) & np.isfinite(b)
           & (np.abs(y) <= BOUND) & (np.abs(a) > 1e-9) & (np.abs(b) > 1e-9))
    n = int(msk.sum())
    if n < MIN_N:
        print('  样本不足 %d' % n)
        summary.append((tgt, nq, dq, C, n, None))
        continue

    yy, aa, bb = y[msk], a[msk], b[msk]
    lhs = yy * bb
    rhs = C * aa
    rel = np.abs(lhs - rhs) / np.abs(rhs)
    sgn = float(np.mean(np.sign(lhs) == np.sign(rhs)))

    q = np.nanpercentile(rel, [50, 75, 90, 95, 99])
    print('  样本 n=%d   符号一致率=%.4f' % (n, sgn))
    print('  相对误差分位: p50=%.3e  p75=%.3e  p90=%.3e  p95=%.3e  p99=%.3e'
          % tuple(q))
    for th in (0.001, 0.01, 0.05, 0.20):
        print('      |rel| < %-6.3f 命中率 = %.4f' % (th, float(np.mean(rel < th))))

    # 直接恒等式命中率（不引入 C，检验 C 本身是否正确）
    ident = float(np.mean(np.abs(lhs - aa) <= 1e-6 * np.maximum(np.abs(aa), 1.0)))
    print('      C=1 恒等命中率 = %.4f   （若≈1 说明 C 应为 1 而非 %g）' % (ident, C))

    # 分年度稳定性
    yr = pd.to_datetime(m.loc[msk, 'end_date'], format='mixed', errors='coerce').dt.year
    tmp = pd.DataFrame({'y': yr.to_numpy(), 'rel': rel})
    g = tmp.groupby('y')['rel']
    med = g.median()
    ok = med < 0.01
    print('  分年度中位相对误差: 最小=%.3e 最大=%.3e  中位=%.3e'
          % (med.min(), med.max(), med.median()))
    print('      <1%% 的年份: %d / %d   最差年份: %s (%.3e)'
          % (int(ok.sum()), len(med),
             int(med.idxmax()) if len(med) else '-',
             float(med.max()) if len(med) else float('nan')))
    worst = med.sort_values(ascending=False).head(3)
    print('      偏差最大 3 年: %s'
          % ', '.join('%d=%.2e' % (int(i), float(v)) for i, v in worst.items()))

    summary.append((tgt, nq, dq, C, n, dict(
        p50=float(q[0]), p90=float(q[2]), sgn=sgn,
        h1=float(np.mean(rel < 0.01)), h5=float(np.mean(rel < 0.05)),
        yr_ok=int(ok.sum()), yr_n=len(med), yr_max=float(med.max()),
    )))

# ---- 汇总 ----
print()
print('=' * 96)
print('汇总（判据：p50<1e-6 且 符号一致率>0.999 且 |rel|<1% 命中率>0.90 且 年度达标>=90%）')
print('=' * 96)
print('%-20s %-28s %-26s %8s %10s %8s %8s %s'
      % ('目标', '分子', '分母', 'C', 'p50误差', '<1%命中', '符号', '年度'))
allpass = True
for tgt, nq, dq, C, n, s in summary:
    if s is None:
        print('%-20s %-28s %-26s %8g %10s %8s %8s %s'
              % (tgt, nq, dq, C, '样本不足', '-', '-', '-'))
        allpass = False
        continue
    ok = (s['p50'] < 1e-6 and s['sgn'] > 0.999 and s['h1'] > 0.90
          and s['yr_ok'] >= 0.9 * s['yr_n'])
    allpass = allpass and ok
    print('%-20s %-28s %-26s %8g %10.2e %8.4f %8.4f %d/%d %s'
          % (tgt, nq, dq, C, s['p50'], s['h1'], s['sgn'],
             s['yr_ok'], s['yr_n'], 'PASS' if ok else 'FAIL'))
print()
print('总判定: %s' % ('全部通过' if allpass else '存在未通过项，需进一步排查'))
