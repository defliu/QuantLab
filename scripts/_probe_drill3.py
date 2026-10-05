# coding: utf-8
"""Drill 3: what exactly is E's extra content?

Hypothesis H1: E is not "D + newly published reports". For the report periods
D already has, E additionally stores a SECOND row for the same announcement
(the update_flag variant), so E row count ~ 2x D row count.

If H1 holds, the migration question is NOT "does E rewrite history" but
"which of the two same-announcement variants is authoritative". That is a
business-semantics question, not a data-shape question, and it decides whether
replacing the D master silently changes every factor value.

Test per table:
  1. row counts, FULL_KEY counts, distinct (code, end_date, ann_date) counts
  2. exact-match rate: D rows that have a byte-identical twin in E
     (FULL_KEY + every shared value column)
  3. for FULL_KEYs present on both sides, do the sides agree on values?
  4. update_flag distribution of matched vs E-only rows
  5. same-announcement multi-row groups on each side, and whether their values
     differ
"""
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

D = r'D:/astock/finance'
E = r'E:/temp/行情数据'
OUTPATH = r'D:/QuantLab/browse/_finance_drill3.txt'
SEP = '\x1f'

TABLES = {
    'fina_indicator': (['ts_code', 'end_date', 'ann_date'],
                       ['eps', 'roe', 'bps', 'orate', 'q_gr_roe']),
    'balancesheet': (['ts_code', 'end_date', 'report_type', 'ann_date'],
                     ['total_assets', 'total_liab', 'total_hldr_eqy_exc_min_int']),
    'income': (['ts_code', 'end_date', 'report_type', 'ann_date'],
               ['revenue', 'n_income_attr_p', 'operate_profit']),
    'cashflow': (['ts_code', 'end_date', 'report_type', 'ann_date'],
                 ['n_cashflow_act', 'n_incr_cash_cash_equ']),
    'forecast': (['ts_code', 'end_date', 'ann_date', 'type'],
                 ['p_change_min', 'net_profit_min', 'p_change_max']),
    'dividend': (['ts_code', 'end_date', 'ann_date', 'div_proc'],
                 ['cash_div', 'stk_div']),
    'express': (['ts_code', 'end_date', 'ann_date'],
                ['revenue', 'n_income_attr_p']),
}

OUT = open(OUTPATH, 'w', encoding='utf-8')


def w(s=''):
    print(s)
    OUT.write(s + '\n')


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


def norm_flag(s):
    t = s.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    return t.where(t.isin(['0', '1']), 'NA')


def load(side, name, key, vals):
    root = D if side == 'D' else E
    fp = os.path.join(root, name + '.parquet')
    if not os.path.exists(fp):
        return None, None
    have = list(pq.ParquetFile(fp).schema_arrow.names)
    want = [c for c in dict.fromkeys(list(key) + list(vals) +
                                     ['update_flag', 'f_ann_date'])
            if c in have]
    return pd.read_parquet(fp, columns=want), have


def rowhash(d, cols):
    if not cols:
        return pd.Series(['0'] * len(d), index=d.index)
    def norm(x):
        if pd.isna(x):
            return 'NA'
        try:
            f = float(x)
            return '%.6f' % f
        except Exception:
            return str(x).strip()
    return pd.util.hash_pandas_object(
        d[cols].astype(object).map(norm).apply(tuple, axis=1),
        index=False).astype('uint64')


def fk(d, key, have):
    parts = []
    for c in key:
        if c not in have:
            parts.append(pd.Series(['NA'] * len(d), index=d.index))
        elif c in ('end_date', 'ann_date', 'f_ann_date', 'change_date'):
            parts.append(canon_date(d[c]))
        else:
            parts.append(d[c].astype(str).str.strip())
    g = parts[0]
    for p in parts[1:]:
        g = g.str.cat(p, sep=SEP)
    return g.astype(str)


def ann_key(d, key, have):
    """(code, report period, announcement) - the announcement identity."""
    pk = [c for c in key if c not in ('ann_date', 'f_ann_date')]
    ak = [c for c in key if c not in ('type', 'div_proc', 'report_type')]
    ak = [c for c in ak if c in have] or pk
    return fk(d, ak, have)


def main():
    w('#' * 100)
    w('# DRILL 3 - is E = D + same-announcement flag variants?')
    w('#' * 100)

    for name, (key, vals) in TABLES.items():
        w()
        w('=' * 100)
        w(name)
        w('=' * 100)
        dd, dhave = load('D', name, key, vals)
        ee, ehave = load('E', name, key, vals)
        if dd is None or ee is None:
            w('  missing D=%s E=%s' % (dd is not None, ee is not None))
            continue

        shared = [c for c in vals if c in dhave and c in ehave]
        w('  rows: D=%d  E=%d   ratio=%.3f' % (len(dd), len(ee),
                                                len(ee) / max(len(dd), 1)))

        dfk = fk(dd, key, dhave)
        efk = fk(ee, key, ehave)
        w('  FULL_KEY distinct: D=%d  E=%d' % (dfk.nunique(), efk.nunique()))

        # announcement identity
        dak = ann_key(dd, key, dhave)
        eak = ann_key(ee, key, ehave)
        w('  ANNOUNCEMENT-KEY distinct: D=%d  E=%d'
          % (dak.nunique(), eak.nunique()))

        # exact twin: FULL_KEY + values all equal
        dv = rowhash(dd, shared).map(lambda v: '%020d' % v)
        ev = rowhash(ee, shared).map(lambda v: '%020d' % v)
        dtwin = pd.Series(dfk.astype(str).values, index=dd.index) + dv
        etwin = pd.Series(efk.astype(str).values, index=ee.index) + ev
        dset = set(dtwin.tolist())
        matched = int(dtwin.isin(dset).sum())
        w('  D rows with an EXACT twin in E (same FULL_KEY + values): '
          '%d / %d = %.2f%%' % (matched, len(dd),
                                100.0 * matched / max(len(dd), 1)))

        eset_d = set(dtwin.tolist())
        ematched = int(etwin.isin(eset_d).sum())
        w('  E rows byte-identical to some D row: %d / %d = %.2f%%'
          % (ematched, len(ee), 100.0 * ematched / max(len(ee), 1)))

        # update_flag distribution
        if 'update_flag' in dhave:
            w('  D update_flag: %s'
              % norm_flag(dd['update_flag']).value_counts().to_dict())
        if 'update_flag' in ehave:
            w('  E update_flag: %s'
              % norm_flag(ee['update_flag']).value_counts().to_dict())

        # multi-row announcements (same code+period+ann_date, several rows)
        for side, d, ak, hv in (('D', dd, dak, dhave),
                                ('E', ee, eak, ehave)):
            g = d.assign(_k=ak.values)
            sz = g.groupby('_k', sort=False).size()
            multi = sz[sz > 1]
            rows_in = int(multi.sum())
            w('  [%s] announcement-keys with >1 row: %d (rows involved %d)'
              % (side, len(multi), rows_in))
            if len(multi) and side == 'E' and 'update_flag' in hv:
                sel = g[g['_k'].isin(set(multi.index))]
                w('      E multi-row flags: %s'
                  % norm_flag(sel['update_flag']).value_counts().to_dict())

    w()
    w('#' * 100)
    w('# DONE')
    w('#' * 100)
    OUT.close()


if __name__ == '__main__':
    main()