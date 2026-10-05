# coding=utf-8
"""列出 income/cashflow/balancesheet 中所有利润、税、收入、现金相关列名。"""
import os
import re
import sys

import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

D_FIN = r'D:\astock\finance'
PAT = re.compile(r'profit|income|tax|revenue|cost|deduct|operate|cashflow|minority|attr|ebit|gp_|_gp|gs_|q_', re.I)

for t in ['income', 'cashflow', 'balancesheet', 'fina_indicator']:
    p = os.path.join(D_FIN, t + '.parquet')
    names = pq.ParquetFile(p).schema_arrow.names
    hit = [c for c in names if PAT.search(c)]
    print('=' * 80)
    print('%s  总列=%d  命中=%d' % (t, len(names), len(hit)))
    print(hit)
    print()
