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

print('=== minute-line coverage probe (index-aware) ===')

for d in ['min_1min', 'min_5min', 'min_15min', 'min_30min', 'min_60min']:
    p = os.path.join(D, d)
    files = sorted(glob.glob(os.path.join(p, '*.parquet')))
    if not files:
        print(d, 'no parquet')
        continue
    try:
        df = pd.read_parquet(files[0])
    except Exception as e:
        print(d, 'READ FAIL', e)
        continue

    tcol = 'trade_time' if 'trade_time' in df.columns else None

    if tcol:
        tv = pd.to_datetime(df[tcol], errors='coerce')
    elif df.index.name and 'time' in str(df.index.name).lower():
        tv = pd.to_datetime(pd.Series(df.index), errors='coerce')
    elif isinstance(df.index, pd.DatetimeIndex):
        tv = pd.Series(df.index)
    else:
        tv = pd.to_datetime(pd.Series(df.index.astype(str)), errors='coerce')

    ok = tv.dropna()
    if len(ok) == 0:
        print(d, 'no parsable time; index name =', df.index.name)
        continue

    print(
        d.ljust(10, ' ')
        + ' files=' + str(len(files)).rjust(5, ' ')
        + '  idx=' + str(df.index.name)
        + '  rows=' + str(len(df)).rjust(8, ' ')
        + '  ' + str(ok.min())[:19] + ' -> ' + str(ok.max())[:19]
    )

print('')
print('=== daily coverage probe ===')
dp = os.path.join(D, 'daily', 'stock_daily.parquet')
df = pd.read_parquet(dp)
print('index name =', df.index.name, '| dtype =', df.index.dtype)
print('rows =', len(df))
if isinstance(df.index, pd.DatetimeIndex) or 'date' in str(df.index.name).lower():
    iv = pd.to_datetime(pd.Series(df.index), errors='coerce').dropna()
    print('daily', str(iv.min())[:19], '->', str(iv.max())[:19])
elif 'trade_date' in df.columns:
    print('daily', df['trade_date'].min(), '->', df['trade_date'].max())
else:
    print('index sample:', list(df.index[:3]))
    print('cols:', list(df.columns)[:6], '...')