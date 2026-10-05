# coding: utf-8
"""Targeted drill-down on the two anomalies the wide PIT probe surfaced.

A. SAME-PERIOD MULTI-VERSION
   fina_indicator 002781.SZ at asof=20151231 -> D roe=8.2628  E roe=15.4971
   Both sides claim visibility at that date. Either E restated history
   (dangerous: a past backtest would change) or D is the stale/partial one.
   Print EVERY (end_date, ann_date, f_ann_date, update_flag, value) row on
   both sides so the version chain is visible, not inferred.

B. D-SIDE 20260331 HOLE
   At asof=20260630, 43 cashflow codes resolved to end=20251231 on D but
   end=20260331 on E, while D reports period max=20260630. So D is NOT
   missing 2026 entirely - it is missing (or mis-dated) the Q1 window.
   Count rows per report period per side and per ann_date month.
"""
import os
import gc
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

D = r'D:/astock/finance'
E = r'E:/temp/行情数据'
OUT = open(r'D:/QuantLab/browse/_finance_drill.txt', 'w', encoding='utf-8')
SEP = '\x1f'
DATE_COLS = ('end_date', 'ann_date', 'change_date', 'f_ann_date')


def w(s=''):
    print(s)
    OUT.write(s + '\n')


def norm_flag(s):
    t = s.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    return t.where(t.isin(['0', '1']), 'NA')


def canon_date(s):
    t = s.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    t = t.where(~t.isin(['nan', 'NaT', 'None', '']), 'NA')
    compact = t.str.replace('-', '', regex=False).str.replace(
        '/', '', regex=False)
    ok = compact.str.match(r'^\d{8}$', na=False)
    out = compact.where(ok, 'NA')
    bad = ~ok & (t != 'NA')
    if bad.any():
        out.loc[bad] = pd.to_datetime(t[bad], errors='coerce').dt.strftime(
            '%Y%m%d').fillna('NA')
    return out


def load(side, name, cols):
    root = D if side == 'D' else E
    fp = os.path.join(root, name + '.parquet')
    have = list(pq.ParquetFile(fp).schema_arrow.names)
    want = [c for c in cols if c in have]
    d = pd.read_parquet(fp, columns=want)
    for c in DATE_COLS + ('update_flag',):
        if c not in d.columns:
            d[c] = 'NA'
    return d, want


# ---------------------------------------------------------------- drill A
def drill_a():
    w('#' * 96)
    w('# DRILL A: same report period, multiple announcement versions')
    w('#' * 96)
    CASES = [
        ('fina_indicator', ['ts_code', 'end_date', 'ann_date', 'f_ann_date',
                            'update_flag', 'roe', 'eps', 'bps'],
         ['002781.SZ', '600105.SH', '603169.SH']),
        ('cashflow', ['ts_code', 'end_date', 'ann_date', 'f_ann_date',
                      'update_flag', 'n_cashflow_act'],
         ['831406.BJ', '600893.SH']),
    ]
    for name, cols, codes in CASES:
        w()
        w('=' * 96)
        w('%s' % name)
        w('=' * 96)
        dd, have = load('D', name, cols)
        ed, _ = load('E', name, cols)
        for code in codes:
            w()
            w('  --- %s ---' % code)
            for side, d in (('D', dd), ('E', ed)):
                sub = d[d['ts_code'].astype(str) == code].copy()
                if sub.empty:
                    w('    [%s] NO ROWS' % side)
                    continue
                sub['end_c'] = canon_date(sub['end_date'])
                sub['ann_c'] = canon_date(sub['ann_date'])
                sub['fann_c'] = canon_date(sub['f_ann_date'])
                sub['flg'] = norm_flag(sub['update_flag'])
                valcols = [c for c in cols
                           if c not in ('ts_code', 'end_date', 'ann_date',
                                        'f_ann_date', 'update_flag')]
                sub = sub.sort_values(['end_c', 'ann_c', 'fann_c'])
                w('    [%s] rows=%d' % (side, len(sub)))
                w('        end       ann       f_ann     flag | %s'
                  % ' | '.join('%-14s' % c for c in valcols))
                for _, r in sub.iterrows():
                    vals = ' | '.join(
                        '%-14s' % ('' if pd.isna(r[c])
                                   else ('%.6g' % float(r[c])
                                         if _num(r[c]) else str(r[c])))
                        for c in valcols)
                    w('        %-9s %-9s %-9s %-4s | %s'
                      % (r['end_c'], r['ann_c'], r['fann_c'], r['flg'], vals))
        del dd, ed
        gc.collect()


def _num(v):
    try:
        float(v)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- drill B
PERIODS = ['20241231', '20250331', '20250630', '20250930', '20251231',
           '20260331', '20260630']


def drill_b():
    w()
    w('#' * 96)
    w('# DRILL B: per-report-period row counts and ann_date month histogram')
    w('#' * 96)
    TABLES = {
        'fina_indicator': (['ts_code', 'end_date', 'ann_date', 'roe'], 'roe'),
        'balancesheet': (['ts_code', 'end_date', 'ann_date', 'total_assets'],
                         'total_assets'),
        'income': (['ts_code', 'end_date', 'ann_date', 'revenue'], 'revenue'),
        'cashflow': (['ts_code', 'end_date', 'ann_date', 'n_cashflow_act'],
                     'n_cashflow_act'),
    }
    for name, (cols, vcol) in TABLES.items():
        w()
        w('=' * 96)
        w('%s   (value col: %s)' % (name, vcol))
        w('=' * 96)
        dd, _ = load('D', name, cols)
        ed, _ = load('E', name, cols)
        for side, d in (('D', dd), ('E', ed)):
            d['_end'] = canon_date(d['end_date'])
            d['_ann'] = canon_date(d['ann_date'])
            w('  [%s] total rows=%d' % (side, len(d)))
            w('        %-10s %10s %10s %10s   %s'
              % ('period', 'rows', 'codes', 'w/ value', 'ann_months'))
            for p in PERIODS:
                sub = d[d['_end'] == p]
                if sub.empty:
                    w('        %-10s %10d %10s %10s   %s'
                      % (p, 0, '-', '-', ''))
                    continue
                nv = int(sub[vcol].notna().sum()) if vcol in sub.columns \
                    else -1
                am = sub.loc[sub[vcol].notna(), '_ann'] \
                    if vcol in sub.columns else sub['_ann']
                am = am[am != 'NA']
                hist = am.str[:6].value_counts().sort_index()
                hs = ' '.join('%s:%d' % (k, v) for k, v in hist.items())
                w('        %-10s %10d %10d %10d   %s'
                  % (p, len(sub), sub['ts_code'].nunique(), nv, hs))
        # cross-side: codes present in E for 20260331 but absent in D
        dd['_end'] = canon_date(dd['end_date'])
        ed['_end'] = canon_date(ed['end_date'])
        for p in ('20260331', '20260630'):
            cs = set(dd.loc[dd['_end'] == p, 'ts_code'].astype(str))
            ce = set(ed.loc[ed['_end'] == p, 'ts_code'].astype(str))
            w('  >> %s  D_codes=%d E_codes=%d  E-not-D=%d  D-not-E=%d'
              % (p, len(cs), len(ce), len(ce - cs), len(cs - ce)))
            if ce - cs:
                w('        sample E-not-D: %s'
                  % sorted(ce - cs)[:12])
        del dd, ed
        gc.collect()


# ---------------------------------------------------------------- drill C
def drill_c():
    w()
    w('#' * 96)
    w('# DRILL C: is the 2026Q1 gap an ANN-DATE problem or a ROW problem?')
    w('#' * 96)
    w('For cashflow, codes that D resolved to 20251231 at asof=20260630')
    w('but E resolved to 20260331: does D hold the 20260331 ROW at all,')
    w('and if yes what ann_date does it carry?')
    cols = ['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'update_flag',
            'n_cashflow_act']
    dd, _ = load('D', 'cashflow', cols)
    ed, _ = load('E', 'cashflow', cols)
    for d in (dd, ed):
        d['_end'] = canon_date(d['end_date'])
        d['_ann'] = canon_date(d['ann_date'])
    # codes E can see 20260331 by 20260630
    ev = ed[(ed['_end'] == '20260331') & (ed['_ann'] <= '20260630')]
    dv = dd[(dd['_end'] == '20260331')]
    codes = sorted(set(ev['ts_code'].astype(str)))
    w('  E: rows(20260331, ann<=20260630)=%d over %d codes'
      % (len(ev), ev['ts_code'].nunique()))
    w('  D: rows(20260331) total=%d over %d codes'
      % (len(dv), dv['ts_code'].nunique()))
    inD = set(dv['ts_code'].astype(str))
    w('  E-visible-20260331 codes that D has the 20260331 row for: %d/%d'
      % (len(inD & set(codes)), len(codes)))
    w('  E-visible-20260331 codes MISSING that row in D: %d'
      % len(set(codes) - inD))
    w()
    w('  ann_date distribution of D 20260331 rows:')
    h = dv['_ann'].value_counts().sort_index()
    for k, v in h.items():
        w('        %-10s %8d' % (k, v))
    w()
    w('  ann_date distribution of E-visible 20260331 rows:')
    h = ev['_ann'].value_counts().sort_index()
    for k, v in h.items():
        w('        %-10s %8d' % (k, v))
    w()
    sample = sorted(set(codes) - inD)[:6]
    for c in sample:
        w('  --- %s ---' % c)
        for side, d in (('D', dd), ('E', ed)):
            sub = d[(d['ts_code'].astype(str) == c) &
                    (d['_end'].isin(['20251231', '20260331', '20260630']))]
            w('    [%s]' % side)
            for _, r in sub.sort_values(['_end', '_ann']).iterrows():
                w('        end=%-9s ann=%-9s f_ann=%-9s flag=%-3s '
                  'n_cashflow_act=%s'
                  % (r['_end'], r['_ann'], canon_date(
                      pd.Series([r['f_ann_date']])).iloc[0],
                     norm_flag(pd.Series([r['update_flag']])).iloc[0],
                     r['n_cashflow_act']))
    del dd, ed
    gc.collect()


if __name__ == '__main__':
    drill_a()
    drill_b()
    drill_c()
    OUT.close()
    print('DONE')
