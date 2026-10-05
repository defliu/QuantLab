import os
import gc
import re
import pandas as pd
import pyarrow.parquet as pq

_OUT = open(r'D:\QuantLab\browse\_finance_updflag.txt', 'w',
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
    idx = [c for c in names if str(c).startswith('__index_level_')]
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

CASES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date'],
     ['ts_code', 'end_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date'],
     ['ts_code', 'end_date', 'report_type']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date'],
     ['ts_code', 'end_date', 'report_type']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date'],
     ['ts_code', 'end_date', 'report_type']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type'],
     ['ts_code', 'end_date']),
    ('express', ['ts_code', 'end_date', 'ann_date'],
     ['ts_code', 'end_date']),
]

print('#' * 96)
print('# update_flag role: is it the discriminator for duplicated FULL_KEYs?')
print('#' * 96)

for name, fk, lk in CASES:
    print('')
    print('=' * 96)
    print(name)
    print('=' * 96)
    e = read_cols(os.path.join(E, name + '.parquet'),
                  fk + ['update_flag'])
    d = read_cols(os.path.join(D, name + '.parquet'),
                  fk + ['update_flag'])

    print('')
    print('  update_flag value counts:')
    print('     E: %s' % dict(e['update_flag'].value_counts()))
    print('     D: %s' % dict(d['update_flag'].value_counts()))

    for tag, t in (('E', e), ('D', d)):
        cnt = t.groupby(fk).size()
        multi = cnt[cnt > 1]
        print('')
        print('  [%s] rows=%d uniqFULL=%d surplus=%d groups>1=%d'
              % (tag, len(t), len(cnt), int((multi - 1).sum()),
                 len(multi)))
        if not len(multi):
            continue
        g = t.groupby(fk)['update_flag'].nunique()
        print('  [%s] groups where update_flag itself varies = %d'
              % (tag, int((g > 1).sum())))
        # does keeping only update_flag==1 give unique FULL_KEY?
        keep = t[t['update_flag'] == '1']
        cnt2 = keep.groupby(fk).size()
        print('  [%s] after keep update_flag==1 : rows=%d uniqFULL=%d '
              'still_dup_groups=%d'
              % (tag, len(keep), len(cnt2), int((cnt2 > 1).sum())))
        keep0 = t[t['update_flag'] == '0']
        print('  [%s] update_flag==0 rows=%d (these are superseded versions)'
              % (tag, len(keep0)))

    # cross-side alignment after flag=1 filter
    e1 = set(map(tuple, e[e['update_flag'] == '1'][fk].values))
    d1 = set(map(tuple, d[d['update_flag'] == '1'][fk].values))
    eall = set(map(tuple, e[fk].values))
    dall = set(map(tuple, d[fk].values))
    print('')
    print('  === alignment ===')
    print('  D_all subset of E_all      : %s   (D-only=%d)'
          % (dall <= eall, len(dall - eall)))
    print('  D_flag1 subset of E_flag1  : %s   (D-only=%d)'
          % (d1 <= e1, len(d1 - e1)))
    print('  D_flag1 subset of E_all    : %s   (D-only=%d)'
          % (d1 <= eall, len(d1 - eall)))
    if d1 - e1:
        print('  D_flag1 but not E_flag1, samples:')
        for k in sorted(d1 - e1)[:5]:
            print('     %s' % (list(k),))
    del e, d
    gc.collect()

print('')
print('#' * 96)
print('# PIT safety check on the deduped E side: ann_date coverage vs f_ann_date')
print('#' * 96)

for name in ('fina_indicator', 'balancesheet', 'income', 'cashflow'):
    t = read_cols(os.path.join(E, name + '.parquet'),
                  ['ts_code', 'end_date', 'ann_date', 'f_ann_date',
                   'update_flag'])
    t1 = t[t['update_flag'] == '1']
    print('')
    print('  %-18s all=%-8d flag1=%-8d' % (name, len(t), len(t1)))
    if 'f_ann_date' in t.columns:
        bad = (t1['f_ann_date'] > t1['ann_date']).sum()
        print('     flag1 rows with f_ann_date>ann_date = %d' % int(bad))
        bad2 = (t1['ann_date'] < t1['end_date']).sum()
        print('     flag1 rows with ann_date<end_date  = %d' % int(bad2))
        print('     ann_date range = %s .. %s'
              % (t1['ann_date'].min(), t1['ann_date'].max()))
        print('     f_ann_date range = %s .. %s'
              % (t1['f_ann_date'].min(), t1['f_ann_date'].max()))
    del t, t1
    gc.collect()

_OUT.close()
print('DONE')