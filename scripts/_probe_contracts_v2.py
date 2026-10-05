# -*- coding: utf-8 -*-
"""只读探针：确认 D 侧真实存储契约 + E 侧键列 dtype，供 v2 迁移脚本按事实写契约。"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D = r'D:\astock'
E = r'E:\temp\行情数据'

PAIRS = [
    ('daily/stock_daily', os.path.join(D, 'daily', 'stock_daily.parquet'),
     os.path.join(E, '个股行情全量', 'stock_daily_full.parquet'), ['trade_date', 'ts_code']),
    ('lhb/top_list', os.path.join(D, 'lhb', 'top_list.parquet'),
     os.path.join(E, 'top_list.parquet'), ['trade_date', 'ts_code']),
    ('board/ths_daily', os.path.join(D, 'board', 'ths_daily.parquet'),
     os.path.join(E, 'ths_daily.parquet'), ['trade_date', 'ts_code']),
    ('board_fundflow/moneyflow_ind_ths',
     os.path.join(D, 'board_fundflow', 'moneyflow_ind_ths.parquet'),
     os.path.join(E, 'moneyflow_ind_ths.parquet'), ['trade_date', 'ts_code']),
    ('research/report_rc_daily', os.path.join(D, 'research', 'report_rc_daily.parquet'),
     os.path.join(E, 'report_rc_daily.parquet'),
     ['ts_code', 'report_date', 'org_name', 'author_name', 'report_title', 'quarter']),
    ('basic/stock_basic', os.path.join(D, 'basic', 'stock_basic.parquet'),
     os.path.join(E, 'stock_basic_data.parquet'), ['ts_code']),
]


def contract(path, keys):
    pf = pq.ParquetFile(path)
    names = list(pf.schema_arrow.names)
    rg = pf.metadata.num_row_groups
    t = pf.read_row_group(0).to_pandas()
    idx = list(getattr(t.index, 'names', []) or [])
    print('  rows=%d  row_groups=%d' % (pf.metadata.num_rows, rg))
    print('  physical_cols=%d  pandas_data_cols=%d' % (len(names), len(t.columns)))
    print('  index_names=%s  index_type=%s' % (idx, type(t.index).__name__))
    for k in keys:
        if k in t.columns:
            print('    col %-14s dtype=%s' % (k, t[k].dtype))
        else:
            print('    col %-14s <NOT A COLUMN>' % k)
    if idx:
        for lv in range(len(idx)):
            try:
                v = t.index.get_level_values(lv)
                print('    idx %-14s dtype=%s' % (idx[lv] or '<unnamed>', v.dtype))
            except Exception as exc:
                print('    idx %-14s <err %s>' % (idx[lv], exc))
    return names, idx


for name, dp, ep, keys in PAIRS:
    print('')
    print('=' * 78)
    print(name)
    print('  --- D ---')
    dn, di = contract(dp, keys)
    print('  --- E ---')
    en, ei = contract(ep, keys)
    print('  D_only_cols=%s' % [c for c in dn if c not in en])
    print('  E_only_cols=%s' % [c for c in en if c not in dn])