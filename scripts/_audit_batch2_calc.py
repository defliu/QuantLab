# coding=utf-8
"""第二批财务迁移关键裁决：8 个下游因子字段能否从原始报表项重算。

背景：E 侧 fina_indicator 的 8 个衍生列填充率仅 0.04%~0.30%，
      直接全量替换会静默丢掉下游 run_screen.py 依赖的字段。
      E 侧的「同族承接列」搜索重合样本仅 500 余行，样本量不足以下结论。

本脚本走另一条更硬的路径：验证这 8 个字段能否由 income/cashflow 的
原始报表项按公开财务公式重算。
  阶段 A：在 D 侧用原始项重算，与 D 侧原字段对比 -> 验证公式本身是否正确
  阶段 B：在 E 侧检查原始项填充率，用 E 侧重算与 D 侧原字段对比
          -> 验证 E 版数据是否具备重算能力

只读，不写生产文件。
"""
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'
E_INC = os.path.join(E_ROOT, 'income.parquet')
E_CF = os.path.join(E_ROOT, 'cashflow.parquet')
D_INC = os.path.join(D_FIN, 'income.parquet')
D_CF = os.path.join(D_FIN, 'cashflow.parquet')

KEYS = ['ts_code', 'end_date', 'ann_date']


def num(a):
    return pd.to_numeric(a, errors='coerce').astype('float64')


def safe_div(a, b):
    b = num(b)
    b = b.where(b.abs() > 1e-12)
    return num(a) / b


def load(path, cols):
    pf = pq.ParquetFile(path)
    use = [c for c in cols if c in pf.schema_arrow.names]
    miss = sorted(set(cols) - set(pf.schema_arrow.names))
    df = pf.read(columns=use).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    for c in use:
        if c not in df.columns and c in df.index.names:
            df = df.reset_index()
            break
    if miss:
        print('    [缺列] %s -> %s' % (os.path.basename(path), miss))
    return df, miss


print('=' * 78)
print('阶段 A：D 侧用原始报表项重算，验证公式')
print('=' * 78)

d_inc, mi = load(D_INC, KEYS + ['revenue', 'total_profit', 'income_tax',
                                'n_income', 'report_type'])
d_cf, mc = load(D_CF, KEYS + ['n_cashflow_act', 'report_type'])
print('D income 缺列=%s  cashflow 缺列=%s' % (mi, mc))

# 只取合并报表（report_type=1）
d_inc = d_inc[d_inc['report_type'].astype(str).isin(['1', '1.0'])]
d_cf = d_cf[d_cf['report_type'].astype(str).isin(['1', '1.0'])]

# 关键：merge 前必须按 FULL KEY 去重。
# 财务表同一业务对象有多公告版本，不去重会产生笛卡尔积，
# 配出来的 income/cashflow 行并非同一期数据 -> 相关系数被算成 0。
d_inc = d_inc.drop_duplicates(subset=KEYS, keep='last')
d_cf = d_cf.drop_duplicates(subset=KEYS, keep='last')
print('去重后 D income=%d  cashflow=%d' % (len(d_inc), len(d_cf)))

m = d_inc.merge(d_cf, on=KEYS, how='inner', suffixes=('', '_cf'))
print('D income∩cashflow = %d 行' % len(m))

d_fi, _ = load(os.path.join(D_FIN, 'fina_indicator.parquet'),
               KEYS + ['q_gsprofit_margin', 'q_netprofit_margin',
                       'q_sales_qoq', 'q_netprofit_qoq',
                       'q_dtprofit_to_profit', 'dtprofit_to_profit',
                       'ocf_to_profit', 'ocf_to_or'])
d_fi = d_fi.drop_duplicates(subset=KEYS, keep='last')
m = m.merge(d_fi, on=KEYS, how='inner')
print('再 ∩ fina_indicator = %d 行' % len(m))

rev = num(m['revenue'])
calc = {
    'dtprofit_to_profit': safe_div(m['income_tax'], m['total_profit']),
    'ocf_to_profit': safe_div(m['n_cashflow_act'], m['n_income']),
    'ocf_to_or': safe_div(m['n_cashflow_act'], m['revenue']),
    'q_netprofit_margin': safe_div(m['n_income'], rev),
    'netprofit_margin_cum': safe_div(m['n_income'], rev),
}

print()
print('%-24s %-10s %-10s %-10s %-9s' % ('字段', '样本', 'corr', 'MAE', '容差内'))
print('-' * 78)
for t, cv in calc.items():
    dt = num(m[t]) if t in m.columns else None
    if dt is None:
        continue
    ok = dt.notna() & cv.notna()
    n = int(ok.sum())
    if n < 100:
        print('%-24s %-10d (样本不足)' % (t, n))
        continue
    a = dt[ok].to_numpy()
    b = cv[ok].to_numpy()
    cc = float(np.corrcoef(a, b)[0, 1])
    mae = float(np.mean(np.abs(a - b)))
    hit = float(np.mean(np.abs(a - b) < 1e-6))
    print('%-24s %-10d %-10.4f %-10.6f %-9.2f%%'
          % (t, n, cc, mae, hit * 100))

print()
print('=' * 78)
print('阶段 A2：单季度化验证（Q = 累计 - 上期累计，Q1 直接取累计）')
print('=' * 78)

mm = m.sort_values(KEYS).copy()
# end_date 落盘后已是 datetime，混合类型需用 format='mixed' 兼容
mm['end_date'] = pd.to_datetime(mm['end_date'], format='mixed')
mm['year'] = mm['end_date'].dt.year
mm['md'] = mm['end_date'].dt.strftime('%m-%d')
mm['q_tag'] = np.where(mm['md'] == '03-31', 1,
                       np.where(mm['md'] == '06-30', 2,
                                np.where(mm['md'] == '09-30', 3, 4)))


def to_single_quote(df, col):
    """同年内累计 -> 单季度。

    注意：位移必须按 (ts_code, year) 分组。
    若只 groupby(year)，shift(1) 会跨股票串行（不同 ts_code 混在一组），
    单季度化结果完全错误。
    """
    c = num(df[col])
    g = df.groupby(['ts_code', 'year'], sort=False)
    prev = c.groupby([df['ts_code'], df['year']], sort=False).shift(1)
    out = c - prev
    # Q1 无上期累计，直接等于累计值
    return out.mask(df['q_tag'] == 1, c)


mm['q_rev'] = to_single_quote(mm, 'revenue')
mm['q_ni'] = to_single_quote(mm, 'n_income')
mm['q_tp'] = to_single_quote(mm, 'total_profit')
mm['q_it'] = to_single_quote(mm, 'income_tax')
mm['q_ocf'] = to_single_quote(mm, 'n_cashflow_act')

qcalc = {
    'q_sales_qoq': None,
    'q_netprofit_margin': safe_div(mm['q_ni'], mm['q_rev']),
    'q_dtprofit_to_profit': safe_div(mm['q_it'], mm['q_tp']),
    'q_profit_to_gr': safe_div(mm['q_ni'], mm['q_rev']),
}

# 环比：单季度 vs 同年上一季
gq = mm.groupby('ts_code', sort=False)
mm['q_rev_prev'] = gq['q_rev'].shift(1)
same_year = gq['year'].shift(1).eq(mm['year'])
mm['q_rev_prev'] = mm['q_rev_prev'].where(same_year)
qcalc['q_sales_qoq'] = safe_div(mm['q_rev'], mm['q_rev_prev']) - 1.0
mm['q_ni_prev'] = gq['q_ni'].shift(1)
mm['q_ni_prev'] = mm['q_ni_prev'].where(
    gq['year'].shift(1).eq(mm['year']))
qcalc['q_netprofit_qoq'] = safe_div(mm['q_ni'], mm['q_ni_prev']) - 1.0
# 毛利需要营业成本，E/D income 里找找
for costcol in ['operate_cost', 'cost', 'oper_cost']:
    if costcol in mm.columns:
        mm['q_cost'] = to_single_quote(mm, costcol)
        qcalc['q_gsprofit_margin'] = safe_div(mm['q_rev'] - mm['q_cost'], mm['q_rev'])
        print('毛利成本列采用: %s' % costcol)
        break
else:
    print('!! 未找到营业成本列，q_gsprofit_margin 无法重算')

print()
print('%-24s %-10s %-10s %-12s' % ('单季度字段', '样本', 'corr', 'MAE'))
print('-' * 78)
for t, cv in qcalc.items():
    if cv is None or t not in mm.columns:
        continue
    dt = num(mm[t])
    ok = dt.notna() & cv.notna()
    n = int(ok.sum())
    if n < 100:
        print('%-24s %-10d (样本不足)' % (t, n))
        continue
    a = dt[ok].to_numpy()
    b = cv[ok].to_numpy()
    cc = float(np.corrcoef(a, b)[0, 1])
    mae = float(np.mean(np.abs(a - b)))
    print('%-24s %-10d %-10.4f %-12.6f' % (t, n, cc, mae))

print()
print('=' * 78)
print('阶段 B：E 侧原始报表项填充率（决定能否在 E 上重算）')
print('=' * 78)

e_inc, _ = load(E_INC, KEYS + ['revenue', 'total_profit', 'income_tax',
                               'n_income', 'report_type', 'operate_cost'])
e_cf, _ = load(E_CF, KEYS + ['n_cashflow_act', 'report_type'])
e_inc = e_inc[e_inc['report_type'].astype(str).isin(['1', '1.0'])]
e_cf = e_cf[e_cf['report_type'].astype(str).isin(['1', '1.0'])]
# 同样必须先去重再 merge，否则笛卡尔积会让一切相关性归零
e_inc = e_inc.drop_duplicates(subset=KEYS, keep='last')
e_cf = e_cf.drop_duplicates(subset=KEYS, keep='last')
print('去重后 E income=%d  cashflow=%d' % (len(e_inc), len(e_cf)))
em = e_inc.merge(e_cf, on=KEYS, how='inner', suffixes=('', '_cf'))
print('E income∩cashflow = %d 行' % len(em))
for c in ['revenue', 'total_profit', 'income_tax', 'n_income',
          'n_cashflow_act', 'operate_cost']:
    if c in em.columns:
        nn = int(num(em[c]).notna().sum())
        print('  %-18s 非空 %8d / %8d = %6.2f%%'
              % (c, nn, len(em), 100.0 * nn / max(len(em), 1)))
    else:
        print('  %-18s 【E 侧无此列】' % c)

print()
print('=' * 78)
print('阶段 B2：E 侧重算 vs D 侧原字段（端到端可用性）')
print('=' * 78)
em = em.drop_duplicates(subset=KEYS, keep='last')
dref = d_fi.set_index(KEYS)
em = em.set_index(KEYS)
common = em.index.intersection(dref.index)
print('共同 FULL KEY = %d' % len(common))
e_rev = num(em.loc[common, 'revenue'])
end2end = {
    'dtprofit_to_profit': safe_div(em.loc[common, 'income_tax'],
                                   em.loc[common, 'total_profit']),
    'ocf_to_profit': safe_div(em.loc[common, 'n_cashflow_act'],
                              em.loc[common, 'n_income']),
    'ocf_to_or': safe_div(em.loc[common, 'n_cashflow_act'], e_rev),
    'q_netprofit_margin(cum)': safe_div(em.loc[common, 'n_income'], e_rev),
}
print()
print('%-26s %-10s %-10s %-10s' % ('字段', '样本', 'corr', 'MAE'))
print('-' * 78)
for t, cv in end2end.items():
    dt = num(dref.loc[common, t.split('(')[0]])
    ok = dt.notna() & cv.notna()
    n = int(ok.sum())
    if n < 100:
        print('%-26s %-10d (样本不足)' % (t, n))
        continue
    a = dt[ok].to_numpy()
    b = cv[ok].to_numpy()
    cc = float(np.corrcoef(a, b)[0, 1])
    mae = float(np.mean(np.abs(a - b)))
    print('%-26s %-10d %-10.4f %-10.6f' % (t, n, cc, mae))
