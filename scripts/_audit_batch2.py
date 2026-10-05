# coding=utf-8
"""第二批（8 张财务表）可行性预审：只读，不写生产文件。

三个裁决问题：
  Q1 下游消费的字段，E 侧是否还在（缺一个就静默丢因子）；
  Q2 多公告版本能否裁决（update_flag / f_ann_date 是否可用）；
  Q3 同 LOGIC KEY 多版本时，选哪条才不破坏 PIT。
"""
import os
import sys
import json

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

FIN_ORDER = ['balancesheet', 'cashflow', 'dividend', 'express',
             'fina_indicator', 'forecast', 'income', 'share_change_event']

FINANCE_PATH = os.path.join(D_FIN, 'fina_indicator.parquet')

# run_screen.FIN_FIELDS（research_factor_mining/run_screen.py:101）
FIN_FIELDS = [
    'bps', 'roe', 'gross_margin', 'netprofit_margin', 'ocfps',
    'eps', 'current_ratio', 'quick_ratio', 'debt_to_assets',
    'profit_dedt', 'netprofit_yoy', 'dt_netprofit_yoy', 'or_yoy',
    'ocf_yoy', 'roe_yoy', 'assets_yoy', 'tr_yoy',
    'q_roe', 'q_gsprofit_margin', 'q_netprofit_margin',
    'q_sales_qoq', 'q_netprofit_qoq', 'q_dtprofit_to_profit',
    'q_ocf_to_sales', 'dtprofit_to_profit', 'ocf_to_profit',
    'ocf_to_or',
]

LOGIC_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type'],
    'cashflow': ['ts_code', 'end_date', 'report_type'],
    'income': ['ts_code', 'end_date', 'report_type'],
    'dividend': ['ts_code', 'end_date'],
    'express': ['ts_code', 'end_date'],
    'fina_indicator': ['ts_code', 'end_date'],
    'forecast': ['ts_code', 'end_date'],
    'share_change_event': ['ts_code', 'change_date'],
}

VER_COLS = ['update_flag', 'f_ann_date', 'ann_date', 'end_date']

print('=' * 78)
print('Q1) 下游 26 个字段：E 侧是否还在 + 填充率')
print('=' * 78)

e_fi = pq.ParquetFile(os.path.join(E_ROOT, 'fina_indicator.parquet'))
e_names = set(e_fi.schema_arrow.names)
d_fi = pq.ParquetFile(FINANCE_PATH)
d_names = set(d_fi.schema_arrow.names)

missing_e = [c for c in FIN_FIELDS if c not in e_names]
missing_d = [c for c in FIN_FIELDS if c not in d_names]
print('E 侧缺失字段 (%d): %s' % (len(missing_e), missing_e))
print('D 侧缺失字段 (%d): %s' % (len(missing_d), missing_d))

present = [c for c in FIN_FIELDS if c in e_names and c in d_names]
tbl = e_fi.read(columns=['ts_code'] + present).to_pandas()
rows = len(tbl)
print()
print('E fina_indicator 总行数: %d' % rows)
print('%-26s %14s %14s %10s' % ('字段', 'E非空', 'E填充率', 'D填充率'))
dtbl = d_fi.read(columns=['ts_code'] + present).to_pandas()
drows = len(dtbl)
crit = []
for c in present:
    ne = int(tbl[c].notna().sum())
    nd = int(dtbl[c].notna().sum())
    print('%-26s %14d %13.2f%% %9.2f%%' % (
        c, ne, 100.0 * ne / max(rows, 1), 100.0 * nd / max(drows, 1)))
    re_ = 100.0 * ne / max(rows, 1)
    rd_ = 100.0 * nd / max(drows, 1)
    if re_ < rd_ * 0.5:
        crit.append((c, re_, rd_))

print()
print('!! E 填充率显著低于 D（<50%%）的字段 %d 个:' % len(crit))
for c, re_, rd_ in crit:
    print('   %-26s E=%.2f%%  D=%.2f%%' % (c, re_, rd_))

print()
print('=' * 78)
print('Q2) 多公告版本裁决字段是否可用（E 侧）')
print('=' * 78)
print('%-20s %-6s %-9s %-9s %s' % (
    '表', 'E行数', 'update_flag', 'f_ann_date', 'LOGIC KEY 多版本行占比'))
q2 = {}
for t in FIN_ORDER:
    ep = os.path.join(E_ROOT, t + '.parquet')
    pf = pq.ParquetFile(ep)
    names = set(pf.schema_arrow.names)
    keys = [k for k in LOGIC_KEYS[t] if k in names]
    uf = 'update_flag' in names
    fa = 'f_ann_date' in names
    sub = pf.read(columns=keys).to_pandas()
    n = len(sub)
    dup = 0
    if keys:
        vc = sub.groupby(keys, dropna=False).size()
        dup = int((vc - 1).clip(lower=0).sum())
    pct = 100.0 * dup / max(n, 1)
    q2[t] = dict(rows=n, update_flag=uf, f_ann_date=fa,
                 dup_rows=dup, dup_pct=round(pct, 2), keys=keys)
    print('%-20s %-8d %-9s %-9s %.2f%%' % (
        t, n, uf, fa, pct))

print()
print('=' * 78)
print('Q3) 裁决可行性初判')
print('=' * 78)
for t in FIN_ORDER:
    r = q2[t]
    has_ver = r['update_flag'] or r['f_ann_date']
    if r['dup_pct'] < 0.01:
        verdict = '无多版本，可直接严格替换'
    elif has_ver:
        verdict = '有多版本但有裁决字段，可按优先级合并'
    else:
        verdict = '有多版本且无裁决字段，需人工定规则'
    print('%-20s %s' % (t, verdict))

with open(os.path.join(os.path.dirname(__file__),
                       '_audit_batch2.json'), 'w',
          encoding='utf-8') as f:
    json.dump(dict(
        missing_e=missing_e, missing_d=missing_d,
        critical=crit, q2=q2,
    ), f, ensure_ascii=False, indent=2)
print()
print('报告: scripts/_audit_batch2.json')
