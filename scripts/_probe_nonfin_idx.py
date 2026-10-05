# -*- coding: utf-8 -*-
"""非财务 4 张表索引层结构探查（只读）。"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

PAIRS = [
    ('top_list', r'lhb', 'top_list.parquet', 'top_list.parquet'),
    ('report_rc_daily', r'research', 'report_rc_daily.parquet',
     'report_rc_daily.parquet'),
    ('ths_daily', r'board', 'ths_daily.parquet', 'ths_daily.parquet'),
    ('moneyflow_ind_ths', r'board_fundflow', 'moneyflow_ind_ths.parquet',
     'moneyflow_ind_ths.parquet'),
]

for name, sub, efile, dfile in PAIRS:
    dp = os.path.join(D_BASE, sub, dfile)
    ep = os.path.join(E_ROOT, efile)
    print('=' * 90)
    print('### %s' % name)
    for tag, p in (('D', dp), ('E', ep)):
        f = pq.ParquetFile(p)
        sch = f.schema_arrow
        idx = [f.name for f in sch if f.name in ('__index_level_0__',)]
        cols = sch.names
        print('  %s 路径=%s' % (tag, p))
        print('    schema.names = %s' % (cols,))
        if idx:
            arr = pq.read_table(p, columns=idx)
            col = arr.column(idx[0])
            try:
                vals = col.to_pylist()[:3]
            except Exception:
                vals = list(col)[:3]
            print('    索引列 %s 样本=%s' % (idx[0], vals))
        print('    pandas index_names = %s' % (f'见下方读取',))
    d = pd.read_parquet(dp)
    e = pd.read_parquet(ep)
    print('    D index: names=%s dtype=%s head=%s'
          % (list(d.index.names), d.index.dtype,
             list(d.index[:3])))
    print('    E index: names=%s dtype=%s head=%s'
          % (list(e.index.names), e.index.dtype,
             list(e.index[:3])))
    print('    D columns=%s' % (list(d.columns),))
    print('    E columns=%s' % (list(e.columns),))
    del d, e

print('IDX_DONE')
