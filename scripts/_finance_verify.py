import os
import gc
import re
import pandas as pd
import pyarrow.parquet as pq

_OUT = open(r'D:\QuantLab\browse\_finance_verify.txt', 'w',
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


def read_cols(path, cols):
    f = pq.ParquetFile(path)
    names = [c.name for c in f.schema_arrow]
    idx = [c for c in names if c.startswith('__index_level_')]
    use = idx + [c for c in cols if c in names]
    t = f.read(columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    for c in t.columns:
        if 'date' in str(c):
            t[c] = t[c].map(norm_date)
        else:
            t[c] = t[c].astype(str).str.strip()
    return t


D = r'D:\astock\finance'
E = r'E:\temp\行情数据'

print('#' * 96)
print('# PART 1  forecast: 414 D-only rows -- is it a type-label replacement?')
print('#' * 96)

fk = ['ts_code', 'end_date', 'ann_date', 'type']
d = read_cols(os.path.join(D, 'forecast.parquet'), fk)
e = read_cols(os.path.join(E, 'forecast.parquet'), fk)

dfk = set(map(tuple, d[fk].values))
efk = set(map(tuple, e[fk].values))
do = sorted(dfk - efk)
print('')
print('  D-only FULL_KEY = %d' % len(do))

do_df = pd.DataFrame(do, columns=fk)
print('  D-only type distribution:')
for t, n in do_df['type'].value_counts().items():
    print('     %-10s %6d' % (t, n))
print('  D-only end_date distribution:')
for t, n in do_df['end_date'].value_counts().head(8).items():
    print('     %-10s %6d' % (t, n))
print('  D-only ann_date range: %s .. %s'
      % (do_df['ann_date'].min(), do_df['ann_date'].max()))

tri = fk[:3]
etr = set(map(tuple, e[tri].values))
print('')
print('  CHECK A: same (ts_code,end_date,ann_date) exists in E with different type?')
matched = 0
unmatched = []
repl = []
for r in do:
    k3 = tuple(r[:3])
    if k3 in etr:
        matched += 1
        types = sorted(set(e[(e['ts_code'] == k3[0]) &
                             (e['end_date'] == k3[1]) &
                             (e['ann_date'] == k3[2])]['type']))
        repl.append((k3[0], k3[1], k3[2], r[3], types))
    else:
        unmatched.append(k3)
print('     matched   = %d' % matched)
print('     unmatched = %d' % len(unmatched))
if repl:
    print('  label replacement pairs (D type -> E types), top 15:')
    pair = {}
    for code, ed, ad, dt, ts in repl:
        k = '%s -> %s' % (dt, ','.join(ts))
        pair[k] = pair.get(k, 0) + 1
    for k, v in sorted(pair.items(), key=lambda x: -x[1])[:15]:
        print('     %-40s %6d' % (k, v))
if unmatched:
    print('  unmatched samples:')
    for x in unmatched[:10]:
        print('     %s' % (list(x),))

del d, e
gc.collect()

print('')
print('#' * 96)
print('# PART 2  fina_indicator: 2 D-only rows -- absent from E entirely?')
print('#' * 96)

fk2 = ['ts_code', 'end_date', 'ann_date']
d2 = read_cols(os.path.join(D, 'fina_indicator.parquet'), fk2)
e2 = read_cols(os.path.join(E, 'fina_indicator.parquet'), fk2)
dfk2 = set(map(tuple, d2[fk2].values))
efk2 = set(map(tuple, e2[fk2].values))
do2 = sorted(dfk2 - efk2)
print('')
print('  D-only FULL_KEY = %d' % len(do2))
for r in do2:
    code, ed, ad = r
    in_e_any = e2[(e2['ts_code'] == code)]
    print('')
    print('     key = %s' % (list(r),))
    print('       E rows for this ts_code = %d' % len(in_e_any))
    if len(in_e_any):
        print('       E end_date  = %s'
              % sorted(set(in_e_any['end_date']))[-6:])
        print('       E ann_date  = %s'
              % sorted(set(in_e_any['ann_date']))[-6:])
print('')
print('  code prefix check: A15xxx / A20xxx are B-share style codes')
for code in sorted(set(r[0] for r in do2)):
    print('     %s  D_count=%d  E_count=%d'
          % (code, int((d2['ts_code'] == code).sum()),
             int((e2['ts_code'] == code).sum())))

del d2, e2
gc.collect()

print('')
print('#' * 96)
print('# PART 3  duplicate-row anatomy (same FULL_KEY, multiple physical rows)')
print('#' * 96)

CASES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]

print('')
print('  %-20s %10s %10s %9s %10s %10s'
      % ('table', 'rowsD', 'uniqD', 'dupD', 'rowsE', 'uniqE'))
print('  ' + '-' * 74)

rows = []
for name, fk in CASES:
    f = pq.ParquetFile(os.path.join(D, name + '.parquet'))
    rd = f.metadata.num_rows
    f2 = pq.ParquetFile(os.path.join(E, name + '.parquet'))
    re_ = f2.metadata.num_rows
    d = read_cols(os.path.join(D, name + '.parquet'), fk)
    e = read_cols(os.path.join(E, name + '.parquet'), fk)
    ud = len(d.drop_duplicates(subset=fk))
    ue = len(e.drop_duplicates(subset=fk))
    print('  %-20s %10d %10d %9d %10d %10d'
          % (name, rd, ud, rd - ud, re_, ue))
    rows.append((name, rd, ud, rd - ud, re_, ue, re_ - ue))
    del d, e
    gc.collect()

print('')
print('  dup% D:')
for name, rd, ud, dd, re_, ue, de in rows:
    if rd:
        print('     %-20s %6.2f%%  (dup=%d)' % (name, 100.0 * dd / rd, dd))
print('  dup% E:')
for name, rd, ud, dd, re_, ue, de in rows:
    if re_:
        print('     %-20s %6.2f%%  (dup=%d)' % (name, 100.0 * de / re_, de))

print('')
print('#' * 96)
print('# PART 4  history depth: earliest end_date per side')
print('#' * 96)
print('')
print('  %-20s %-14s %-14s' % ('table', 'D earliest', 'E earliest'))
for name, fk in CASES:
    ycol = 'end_date' if 'end_date' in fk else 'change_date'
    d = read_cols(os.path.join(D, name + '.parquet'), [ycol, 'ts_code'])
    e = read_cols(os.path.join(E, name + '.parquet'), [ycol, 'ts_code'])
    dmin = d.loc[d[ycol] != '<NA>', ycol].min()
    emin = e.loc[e[ycol] != '<NA>', ycol].min()
    dy = int(dmin[:4]) if dmin != '<NA>' else 0
    ey = int(emin[:4]) if emin != '<NA>' else 0
    print('  %-20s %-14s %-14s  E extends back %d years'
          % (name, dmin, emin, max(0, dy - ey)))
    del d, e
    gc.collect()

print('')
print('#' * 96)
print('# PART 5  ts_code universe per side')
print('#' * 96)
print('')
for name, fk in CASES:
    d = read_cols(os.path.join(D, name + '.parquet'), ['ts_code'])
    e = read_cols(os.path.join(E, name + '.parquet'), ['ts_code'])
    dc = set(d['ts_code'])
    ec = set(e['ts_code'])
    print('  %-20s D=%-6d E=%-6d  E-only=%-5d D-only=%-5d'
          % (name, len(dc), len(ec), len(ec - dc), len(dc - ec)))
    if dc - ec:
        print('       D-only codes sample: %s'
              % sorted(dc - ec)[:8])
    del d, e
    gc.collect()

_OUT.close()