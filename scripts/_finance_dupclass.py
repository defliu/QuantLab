import os
import gc
import re
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

_OUT = open(r'D:\QuantLab\browse\_finance_dupclass.txt', 'w',
            encoding='utf-8', buffering=1)
_p = print


def print(*a, **k):
    k['file'] = _OUT
    k.setdefault('flush', True)
    _p(*a, **k)


_ISO = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
_DIG = re.compile(r'\d+')


def norm_date(v):
    s = str(v).strip()
    if not s or s in ('nan', 'NaT', 'None', '<NA>'):
        return '<NA>'
    m = _ISO.match(s)
    if m:
        return '%s%s%s' % m.groups()
    d = _DIG.sub('', s)
    if len(d) >= 8:
        return d[:8]
    return s


def read_all(path):
    f = pq.ParquetFile(path)
    t = f.read().to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    for c in t.columns:
        if 'date' in str(c):
            t[c] = t[c].map(norm_date)
    return t


def classify_dup(path, fk, label):
    print('')
    print('=' * 96)
    print('%s   FULL_KEY=%s' % (label, fk))
    print('=' * 96)
    t = read_all(path)
    n = len(t)
    ncol = len(t.columns)
    fkset = set(map(tuple, t[fk].values))
    print('')
    print('  rows=%d  cols=%d' % (n, ncol))
    print('  uniq FULL_KEY      = %d' % len(fkset))
    print('  surplus rows       = %d' % (n - len(fkset)))

    strt = t.astype(str)
    rh = pd.util.hash_pandas_object(
        strt, index=False).values.astype('uint64')
    nuniq_row = len(np.unique(rh))
    print('  uniq full-row hash = %d' % nuniq_row)
    if nuniq_row == len(fkset):
        print('  >> VERDICT: duplicates are EXACT byte-identical copies')
        print('     (uniq_row_hash == uniq_FULL_KEY) -> safe dedup')
        exact = True
    else:
        exact = False
        print('  >> VERDICT: duplicates carry DIFFERING values')
        print('     surplus differing rows = %d'
              % (nuniq_row - len(fkset)))
        print('     => same FULL_KEY has multiple value versions')
        t2 = t.copy()
        t2['_rh'] = rh
        g = t2.groupby(fk)['_rh'].nunique()
        multi = g[g > 1]
        print('     FULL_KEYs with >1 distinct value version = %d'
              % len(multi))
        print('     max distinct versions for one FULL_KEY = %d'
              % int(multi.max()) if len(multi) else 0)
        if len(multi):
            print('     top conflicting FULL_KEYs:')
            for k in list(multi.sort_values(ascending=False).index[:8]):
                print('        %s  versions=%d'
                      % (list(k), int(multi.loc[k])))
            yc = 'end_date' if 'end_date' in fk else 'change_date'
            yi = fk.index(yc)
            vc = {}
            for k in multi.index[:20000]:
                v = str(k[yi])[:4]
                vc[v] = vc.get(v, 0) + 1
            print('     conflict end_date year dist (sample): %s'
                  % dict(sorted(vc.items())[:14]))
    del t, strt
    gc.collect()
    return exact


print('#' * 96)
print('# Duplicate anatomy: are repeated FULL_KEYs exact copies or real versions?')
print('#' * 96)

CASES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
]

for name, fk in CASES:
    classify_dup(os.path.join(r'E:\temp\行情数据', name + '.parquet'),
                 fk, 'E-side ' + name)

print('')
print('#' * 96)
print('# Same test on D side (control)')
print('#' * 96)
for name, fk in CASES:
    classify_dup(os.path.join(r'D:\astock\finance', name + '.parquet'),
                 fk, 'D-side ' + name)

_OUT.close()