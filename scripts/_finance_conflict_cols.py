import os
import gc
import re
import pandas as pd
import pyarrow.parquet as pq

_OUT = open(r'D:\QuantLab\browse\_finance_conflict.txt', 'w',
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
        else:
            t[c] = t[c].astype(str).str.strip()
    return t


D = r'D:\astock\finance'
E = r'E:\temp\行情数据'

CASES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
]

print('#' * 96)
print('# A. Column-set diff D vs E')
print('#' * 96)
for name, fk in CASES:
    fp = pq.ParquetFile(os.path.join(E, name + '.parquet'))
    fc = pq.ParquetFile(os.path.join(D, name + '.parquet'))
    en = [c.name for c in fp.schema_arrow]
    dn = [c.name for c in fc.schema_arrow]
    eg = {c for c in en if str(c).startswith('__index_level_')}
    dg = {c for c in dn if str(c).startswith('__index_level_')}
    es = {c for c in en if c not in eg}
    ds = {c for c in dn if c not in dg}
    print('')
    print('  %-18s D=%d E=%d  ghostD=%s ghostE=%s'
          % (name, len(ds), len(es), sorted(dg), sorted(eg)))
    if es - ds:
        print('     E-only cols (%d): %s'
              % (len(es - ds), sorted(es - ds)))
    if ds - es:
        print('     D-only cols (%d): %s'
              % (len(ds - es), sorted(ds - es)))

print('')
print('#' * 96)
print('# B. Which columns actually differ inside a duplicated FULL_KEY?')
print('#   (sample up to 800 conflicting FULL_KEYs per table)')
print('#' * 96)

for name, fk in CASES:
    print('')
    print('=' * 96)
    print('E-side %s' % name)
    print('=' * 96)
    t = read_all(os.path.join(E, name + '.parquet'))
    print('')
    print('  rows=%d cols=%d' % (len(t), len(t.columns)))
    cnt = t.groupby(fk).size()
    multi = cnt[cnt > 1]
    print('  FULL_KEY groups       = %d' % len(cnt))
    print('  groups with >1 row    = %d' % len(multi))
    print('  surplus rows          = %d' % int((multi - 1).sum()))
    if not len(multi):
        del t
        gc.collect()
        continue

    keys = list(multi.sort_values(ascending=False).index[:800])
    sub = t[t[fk].apply(tuple, axis=1).isin(set(keys))]
    print('  sampled rows          = %d (from %d groups)'
          % (len(sub), len(keys)))

    g = sub.groupby(fk)
    conflict = []
    for c in t.columns:
        if c in fk:
            continue
        try:
            n = g[c].nunique()
        except Exception:
            continue
        if int((n > 1).sum()):
            conflict.append((c, int((n > 1).sum())))
    print('  columns that vary     = %d' % len(conflict))
    if conflict:
        conflict.sort(key=lambda x: -x[1])
        print('     %-32s %8s %10s' % ('column', 'n_group', 'pct'))
        for c, n in conflict[:25]:
            print('     %-32s %8d %9.1f%%'
                  % (c, n, 100.0 * n / len(sub)))
        if len(conflict) > 25:
            print('     ... %d more' % (len(conflict) - 25))
    else:
        print('     (none -- difference is row ORDER only)')
    if conflict:
        print('  detail sample (first 3 keys, up to 6 varying cols):')
        cset = set()
        for k in keys[:3]:
            rows = sub[sub[fk].apply(tuple, axis=1) == k]
            print('     KEY %s' % (list(k),))
            for cn, _n in conflict:
                if cn in cset or len(cset) >= 6:
                    continue
                cset.add(cn)
                vs = list(rows[cn].unique())
                print('        %-26s %s' % (cn, vs[:4]))
    del t, sub
    gc.collect()

_OUT.close()
print('DONE')