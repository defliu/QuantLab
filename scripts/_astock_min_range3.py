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

print('=== minute-line coverage probe (MultiIndex aware) ===')

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

    names = list(df.index.names)
    tv = None
    if isinstance(df.index, pd.MultiIndex):
        # 找名字里含 time 的那一层，否则取最后一层
        lvl = None
        for i, n in enumerate(names):
            if n and 'time' in str(n).lower():
                lvl = i
                break
        if lvl is None:
            lvl = len(names) - 1
        raw = df.index.get_level_values(lvl)
        tv = pd.to_datetime(pd.Series(raw), errors='coerce')
        used = names[lvl]
    elif 'trade_time' in df.columns:
        tv = pd.to_datetime(df['trade_time'], errors='coerce')
        used = 'trade_time'
    else:
        raw = pd.Series(df.index.astype(object))
        tv = pd.to_datetime(raw, errors='coerce')
        used = str(df.index.name)

    ok = tv.dropna()
    if len(ok) == 0:
        print(d, 'no parsable time; names =', names)
        continue

    print(
        d.ljust(10, ' ')
        + ' files=' + str(len(files)).rjust(5, ' ')
        + '  names=' + str(names).ljust(38, ' ')
        + '  lvl=' + str(used).ljust(12, ' ')
        + '  rows=' + str(len(df)).rjust(8, ' ')
        + '  ' + str(ok.min())[:19] + ' -> ' + str(ok.max())[:19]
    )

print('')
print('=== daily coverage probe ===')
dp = os.path.join(D, 'daily', 'stock_daily.parquet')
df = pd.read_parquet(dp)
print('index names =', list(df.index.names))
print('rows =', len(df))

if isinstance(df.index, pd.MultiIndex):
    for i, n in enumerate(df.index.names):
        raw = df.index.get_level_values(i)
        tv = pd.to_datetime(pd.Series(raw), errors='coerce').dropna()
        if len(tv):
            print('  lvl', i, str(n).ljust(14, ' '), str(tv.min())[:19], '->', str(tv.max())[:19])
else:
    print('index dtype =', df.index.dtype)
    print('sample:', list(df.index[:3]))
    if isinstance(df.index, pd.DatetimeIndex):
        print('range:', str(df.index.min())[:19], '->', str(df.index.max())[:19])