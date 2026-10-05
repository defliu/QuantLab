import os
import pandas as pd
import pyarrow.parquet as pq

_OUT = open(r'D:\QuantLab\browse\_finance_diff.txt', 'w', encoding='utf-8')
_p = print


def print(*a, **k):
    k['file'] = _OUT
    _p(*a, **k)


_ISO = __import__('re').compile(r'^(\d{4})-(\d{2})-(\d{2})')


def norm_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return '<NA>'
    s = str(v).strip()
    if not s or s in ('nan', 'NaT', 'None', '<NA>'):
        return '<NA>'
    m = _ISO.match(s)
    if m:
        return '%s%s%s' % m.groups()
    d = ''.join(ch for ch in s if ch.isdigit())
    if len(d) >= 8:
        return d[:8]
    return s


def load(path, keycols):
    t = pq.read_table(path).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns
             if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    for c in t.columns:
        if 'date' in str(c):
            t[c] = t[c].map(norm_date)
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

for name, fk, lk in CASES:
    dp = os.path.join(r'D:\astock\finance', name + '.parquet')
    ep = os.path.join(r'E:\temp\行情数据', name + '.parquet')

    print('')
    print('=' * 100)
    print('%s' % name)
    print('  FULL_KEY  = %s' % fk)
    print('  LOGIC_KEY = %s' % lk)
    print('=' * 100)

    if not (os.path.exists(dp) and os.path.exists(ep)):
        print('  [SKIP] missing side')
        continue

    d = load(dp, fk)
    e = load(ep, fk)

    d = d.drop_duplicates(subset=fk)
    e = e.drop_duplicates(subset=fk)

    dfk = set(map(tuple, d[fk].astype(str).values))
    efk = set(map(tuple, e[fk].astype(str).values))

    dlk = set(map(tuple, d[lk].astype(str).values))
    elk = set(map(tuple, e[lk].astype(str).values))

    print('  rows            D=%-9d E=%-9d delta=%+d'
          % (len(d), len(e), len(e) - len(d)))
    print('  FULL_KEY uniq   D=%-9d E=%-9d delta=%+d'
          % (len(dfk), len(efk), len(efk) - len(dfk)))
    print('  LOGIC_KEY uniq  D=%-9d E=%-9d delta=%+d'
          % (len(dlk), len(elk), len(elk) - len(dlk)))
    print('')
    print('  E-only FULL_KEY  = %d   (E 有 D 无)' % len(efk - dfk))
    print('  D-only FULL_KEY  = %d   (D 有 E 无)' % len(dfk - efk))
    print('  E-only LOGIC_KEY = %d' % len(elk - dlk))
    print('  D-only LOGIC_KEY = %d' % len(dlk - elk))

    ver = d.groupby(lk).size()
    print('  D versions/logic-key: max=%d  >1 keys=%d'
          % (int(ver.max()), int((ver > 1).sum())))
    ver2 = e.groupby(lk).size()
    print('  E versions/logic-key: max=%d  >1 keys=%d'
          % (int(ver2.max()), int((ver2 > 1).sum())))

    both = dfk & efk
    print('  shared FULL_KEY  = %d' % len(both))

    if efk - dfk:
        sub = sorted(efk - dfk)[:6]
        print('')
        print('  sample E-only FULL_KEY:')
        for s in sub:
            print('     %s' % (list(s),))

    dl = d.copy()
    el = e.copy()
    dl['end_date'] = dl.get('end_date', dl.get('change_date'))
    el['end_date'] = el.get('end_date', el.get('change_date'))

    print('')
    print('  rows by end_date year (E-only FULL_KEY distribution):')
    eo = e[[c for c in fk]].astype(str)
    eo = eo[[c for c in eo.columns]]
    eonly = eo[[tuple(s) in (efk - dfk) for s in
                eo[fk].values]]
    ycol = 'end_date' if 'end_date' in fk else 'change_date'
    try:
        vc = eonly[ycol].str.slice(0, 4).value_counts().sort_index()
        for y, n in vc.items():
            print('     %s  %6d' % (y, n))
    except Exception as ex:
        print('     [ERR] %s' % ex)

    del d, e

_OUT.close()