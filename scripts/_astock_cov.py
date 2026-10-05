# -*- coding: utf-8 -*-
import os
import sys
import glob

import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D = r'D:\astock'

print('=== minute-line coverage (metadata only, no full load) ===', flush=True)

for d in ['min_1min', 'min_5min', 'min_15min', 'min_30min', 'min_60min']:
    p = os.path.join(D, d)
    files = sorted(glob.glob(os.path.join(p, '*.parquet')))
    if not files:
        print(d, 'no parquet', flush=True)
        continue

    f0 = files[0]
    try:
        pf = pq.ParquetFile(f0)
        names = list(pf.schema_arrow.names) if pf.schema_arrow.names else []
        idx_names = [n for n in (pf.metadata.metadata or {}) if False]
        md_index = None
        try:
            mi = pf.metadata
            # pandas 写入的 index 通常在 metadata 里
            kv = pf.schema_arrow.metadata or {}
            for k, v in kv.items():
                if k == b'pandas':
                    mi = v.decode('utf-8', 'replace')
        except Exception:
            mi = None

        df_head = pd.read_parquet(f0).head(0)
        print(
            d.ljust(10, ' ')
            + ' files=' + str(len(files)).rjust(5, ' ')
            + '  rows(1st)=' + str(pf.metadata.num_rows).rjust(9, ' ')
            + '  cols=' + str(len(names)).rjust(3, ' ')
            + '  idx_names=' + str(list(df_head.index.names)).ljust(30, ' '),
            flush=True,
        )
    except Exception as e:
        print(d, 'FAIL', e, flush=True)

print('', flush=True)
print('=== daily coverage (row-group column stats, no full load) ===', flush=True)

dp = os.path.join(D, 'daily', 'stock_daily.parquet')
try:
    pf = pq.ParquetFile(dp)
    print('rows =', pf.metadata.num_rows, flush=True)
    print('row_groups =', pf.metadata.num_row_groups, flush=True)
    print('columns =', pf.schema_arrow.names, flush=True)

    # 找日期列的统计信息
    schema_names = pf.schema_arrow.names
    for cand in ['trade_date', 'date', 'cal_date', 'datetime']:
        if cand in schema_names:
            ci = schema_names.index(cand)
            mins, maxs = [], []
            for rg in range(pf.metadata.num_row_groups):
                st = pf.metadata.row_group(rg).column(ci).statistics
                if st is not None and st.has_min_max:
                    mins.append(st.min)
                    maxs.append(st.max)
            if mins:
                print(
                    '  ' + cand + ': ' + str(min(mins)) + ' -> ' + str(max(maxs)),
                    flush=True,
                )
except Exception as e:
    print('daily stats fail:', e, flush=True)