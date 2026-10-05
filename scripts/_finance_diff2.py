import os
import gc
import re
import pandas as pd
import pyarrow.parquet as pq

_OUT = open(r'D:\QuantLab\browse\_finance_diff.txt', 'w',
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


def read_keys(path, keycols):
    f = pq.ParquetFile(path)
    names = [c.name for c in f.schema_arrow]
    idx_cols = [c for c in names if c.startswith('__index_level_')]
    cols = []
    for c in list(keycols):
        if c not in names and c in idx_cols:
            cols.append(c)
        elif c in names:
            cols.append(c)
    cols = idx_cols + [c for c in cols if c not in idx_cols]
    t = f.read(columns=cols).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    t = t.rename(columns={c: c for c in t.columns})
    for c in t.columns:
        if 'date' in str(c):
            t[c] = t[c].map(norm_date)
        else:
            t[c] = t[c].astype(str).str.strip()
    return t


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
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc'],
     ['ts_code', 'end_date']),
    ('express', ['ts_code', 'end_date', 'ann_date'],
     ['ts_code', 'end_date']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason'],
     ['ts_code', 'change_date']),
]

summary = []

for name, fk, lk in CASES:
    dp = os.path.join(r'D:\astock\finance', name + '.parquet')
    ep = os.path.join(r'E:\temp\行情数据', name + '.parquet')

    print('')
    print('=' * 96)
    print(name)
    print('  FULL_KEY  = %s' % fk)
    print('  LOGIC_KEY = %s' % lk)
    print('=' * 96)

    if not (os.path.exists(dp) and os.path.exists(ep)):
        print('  [SKIP] missing side')
        continue

    d = read_keys(dp, fk)
    e = read_keys(ep, fk)

    d = d.drop_duplicates(subset=fk)
    e = e.drop_duplicates(subset=fk)

    dfk = set(map(tuple, d[fk].values))
    efk = set(map(tuple, e[fk].values))
    dlk = set(map(tuple, d[lk].values))
    elk = set(map(tuple, e[lk].values))

    eo_fk = efk - dfk
    do_fk = dfk - efk
    eo_lk = elk - dlk
    do_lk = dlk - elk

    dv = d.groupby(lk).size()
    ev = e.groupby(lk).size()

    print('  rows            D=%-9d E=%-9d delta=%+d'
          % (len(d), len(e), len(e) - len(d)))
    print('  FULL_KEY uniq   D=%-9d E=%-9d delta=%+d'
          % (len(dfk), len(efk), len(efk) - len(dfk)))
    print('  LOGIC_KEY uniq  D=%-9d E=%-9d delta=%+d'
          % (len(dlk), len(elk), len(elk) - len(dlk)))
    print('')
    print('  shared FULL_KEY      = %d' % len(dfk & efk))
    print('  E-only  FULL_KEY     = %d   (E新增/新公告版本)' % len(eo_fk))
    print('  D-only  FULL_KEY     = %d   (E缺失/旧公告版本)' % len(do_fk))
    print('  E-only  LOGIC_KEY    = %d   (E新增报告期)' % len(eo_lk))
    print('  D-only  LOGIC_KEY    = %d   (E缺失报告期)' % len(do_lk))
    print('')
    print('  D versions/logic-key : max=%-4d  keys_with_multi=%d'
          % (int(dv.max()), int((dv > 1).sum())))
    print('  E versions/logic-key : max=%-4d  keys_with_multi=%d'
          % (int(ev.max()), int((ev > 1).sum())))

    ycol = 'end_date' if 'end_date' in fk else 'change_date'
    yi = fk.index(ycol)

    def ydist(keys):
        vc = {}
        for k in keys:
            v = str(k[yi])[:4]
            vc[v] = vc.get(v, 0) + 1
        return vc

    if do_fk:
        print('')
        print('  D-only FULL_KEY by end_date year:')
        for y, n in sorted(ydist(do_fk).items()):
            print('     %s  %7d' % (y, n))
        s = sorted(do_fk)[:5]
        print('  sample D-only FULL_KEY:')
        for x in s:
            print('     %s' % (list(x),))

    if eo_fk:
        print('')
        print('  E-only FULL_KEY by end_date year (top 12):')
        dd = sorted(ydist(eo_fk).items())
        for y, n in dd[:12]:
            print('     %s  %7d' % (y, n))
        if len(dd) > 12:
            print('     ... %d more years' % (len(dd) - 12))
        s = sorted(eo_fk)[:5]
        print('  sample E-only FULL_KEY:')
        for x in s:
            print('     %s' % (list(x),))

    if do_lk:
        print('')
        print('  D-only LOGIC_KEY by end_date year:')
        yl = lk.index(ycol)
        vc = {}
        for k in do_lk:
            v = str(k[yl])[:4]
            vc[v] = vc.get(v, 0) + 1
        for y, n in sorted(vc.items()):
            print('     %s  %7d' % (y, n))
        print('  sample D-only LOGIC_KEY:')
        for x in sorted(do_lk)[:5]:
            print('     %s' % (list(x),))

    summary.append((name, len(d), len(e), len(dfk), len(efk),
                    len(eo_fk), len(do_fk), len(eo_lk), len(do_lk)))

    del d, e
    gc.collect()

print('')
print('#' * 96)
print('SUMMARY  table  rowsD  rowsE  fkD  fkE  EonlyFK  DonlyFK  EonlyLK  DonlyLK')
for s in summary:
    print('%-20s %8d %7d %6d %6d %8d %8d %8d %8d' % s)
print('#' * 96)

_OUT.close()