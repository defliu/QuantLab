# -*- coding: utf-8 -*-
import os
import sys
import glob

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D = r'D:\astock'

print('=== minute-line coverage probe ===')

for d in ['min_1min', 'min_5min', 'min_15min', 'min_30min', 'min_60min']:
    p = os.path.join(D, d)
    if not os.path.isdir(p):
        print(d, 'MISSING')
        continue
    files = sorted(glob.glob(os.path.join(p, '*.parquet')))
    if not files:
        print(d, 'no parquet')
        continue
    try:
        df = pd.read_parquet(files[0])
    except Exception as e:
        print(d, 'READ FAIL', e)
        continue

    tcol = 'trade_time' if 'trade_time' in df.columns else df.columns[0]
    try:
        tmin = pd.to_datetime(df[tcol]).min()
        tmax = pd.to_datetime(df[tcol]).max()
        print(
            d.ljust(10, ' ')
            + ' files=' + str(len(files)).rjust(5, ' ')
            + '  sample=' + os.path.basename(files[0])[:12]
            + '  rows=' + str(len(df)).rjust(8, ' ')
            + '  ' + str(tmin)[:19] + '  ->  ' + str(tmax)[:19]
            + '  cols=' + str(len(df.columns))
        )
    except Exception as e:
        print(d, 'PARSE FAIL', e, list(df.columns)[:8])

print('')
print('=== daily coverage probe ===')
dp = os.path.join(D, 'daily', 'stock_daily.parquet')
try:
    df = pd.read_parquet(dp)
    dcol = 'trade_date' if 'trade_date' in df.columns else None
    if dcol:
        print('daily rows =', len(df))
        print('daily trade_date', df[dcol].min(), '->', df[dcol].max())
    else:
        print('daily cols:', list(df.columns))
except Exception as e:
    print('daily read fail:', e)