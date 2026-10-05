# coding: utf-8
"""Follow-up probe: coverage cutoff, real reader-tie risk, wide PIT comparison.

Fixes / additions vs _probe_safe_dedup.py:

* BUGFIX  the previous "same end_date but different ann_date" metric grouped by
  a key that ALREADY contained ann_date, so ann.nunique() was structurally 1
  -> the metric was vacuous. The real reader risk is: for one report period
  (ts_code, end_date[, report_type]) there exist several DISTINCT ann_date.
  The production reader does `end_dates.idxmax()` which returns the FIRST of
  the tied rows, i.e. it may silently return a superseded revision.

* COVERAGE  is D merely older, or does E rewrite history? Compare per side the
  min/max ann_date and max end_date per table.

* WIDE PIT  the 8-code sample showed diffs only at asof=20260831. Test whether
  that is purely a freshness gap by (a) running over a much wider code set and
  (b) splitting asof dates into <= D-cutoff and > D-cutoff.
"""
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

D = r'D:/astock/finance'
E = r'E:/temp/行情数据'
OUTPATH = r'D:/QuantLab/browse/_finance_pit_wide.txt'
SEP = '\x1f'
DATE_COLS = ('end_date', 'ann_date', 'change_date', 'f_ann_date')

TABLES = {
    'fina_indicator': (['ts_code', 'end_date', 'ann_date'],
                       ['eps', 'roe', 'bps']),
    'balancesheet': (['ts_code', 'end_date', 'report_type', 'ann_date'],
                     ['total_assets', 'total_liab']),
    'income': (['ts_code', 'end_date', 'report_type', 'ann_date'],
               ['revenue', 'n_income_attr_p']),
    'cashflow': (['ts_code', 'end_date', 'report_type', 'ann_date'],
                 ['n_cashflow_act']),
    'forecast': (['ts_code', 'end_date', 'ann_date', 'type'],
                 ['p_change_min', 'net_profit_min']),
    'dividend': (['ts_code', 'end_date', 'ann_date', 'div_proc'],
                 ['cash_div']),
    'express': (['ts_code', 'end_date', 'ann_date'],
                ['revenue', 'n_income_attr_p']),
    'share_change_event': (['ts_code', 'change_date', 'change_reason'],
                           ['change_ratio']),
}

OUT = open(OUTPATH, 'w', encoding='utf-8')


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


def canon_str(s):
    t = s.astype(str).str.strip()
    return t.where(~t.isin(['nan', 'NaT', 'None', '']), 'NA')


def canon_col(s, col):
    return canon_date(s) if col in DATE_COLS else canon_str(s)


def group_key(d, key):
    parts = [canon_col(d[c], c) for c in key]
    g = parts[0]
    for p in parts[1:]:
        g = g.str.cat(p, sep=SEP)
    return g.astype(str)


def load(side, name, key, vals):
    root = D if side == 'D' else E
    fp = os.path.join(root, name + '.parquet')
    if not os.path.exists(fp):
        return None, None
    have = list(pq.ParquetFile(fp).schema_arrow.names)
    want = [c for c in dict.fromkeys(list(key) + list(vals) +
                                     ['update_flag', 'f_ann_date'])
            if c in have]
    d = pd.read_parquet(fp, columns=want)
    for c in ('end_date', 'ann_date', 'change_date', 'f_ann_date',
              'update_flag'):
        if c not in d.columns:
            d[c] = 'NA'
    return d, have


def rule_r2(d, key, val_cols):
    g = group_key(d, key)
    pri = norm_flag(d['update_flag']).map({'1': 0, '0': 1}).fillna(2)
    ann = canon_date(d['ann_date'])
    fann = canon_date(d['f_ann_date'])
    vcols = [c for c in val_cols if c in d.columns]
    if vcols:
        fp = pd.util.hash_pandas_object(d[vcols].astype(object),
                                        index=False).astype('uint64')
        fp = fp.map(lambda v: '%020d' % v)
    else:
        fp = pd.Series(['0'] * len(d), index=d.index)
    meta = pd.DataFrame({'g': g.values, 'pri': pri.values, 'ann': ann.values,
                         'fann': fann.values, 'fp': fp.values,
                         'ridx': range(len(d))})
    meta = meta.sort_values(['g', 'pri', 'ann', 'fann', 'fp', 'ridx'],
                            ascending=[True, True, False, False, True, True])
    keep = meta.drop_duplicates('g', keep='first')['ridx'].values
    return d.iloc[np.sort(keep)]


def pit_pick(sub, ann_col, asof):
    ann = canon_date(sub[ann_col])
    vis = sub[ann.le(asof)]
    if vis.empty:
        return None
    # report-period column: end_date normally, change_date for share events.
    # 'end_date' may be the 'NA' sentinel injected by load() when the real
    # column is absent, so decide from the non-sentinel content.
    per_col = 'end_date'
    if not (sub['end_date'].astype(str) != 'NA').any():
        per_col = 'change_date'
    end = pd.to_datetime(canon_date(vis[per_col]), format='%Y%m%d',
                         errors='coerce').fillna(pd.Timestamp.min)
    fp = pd.util.hash_pandas_object(vis.astype(object),
                                    index=False).astype('uint64')
    vis = vis.assign(_o=end.values, _a=ann.loc[vis.index].values,
                     _h=fp.values)
    vis = vis.sort_values(['_o', '_a', '_h'],
                          ascending=[False, False, True])
    return vis.iloc[0]


def period_key(d, key):
    """Report-period key WITHOUT ann_date -> the level the reader groups on."""
    pk = [c for c in key if c not in ('ann_date', 'f_ann_date')]
    return group_key(d, pk)


def main():
    w('#' * 100)
    w('# COVERAGE (is E only fresher?) + REAL reader-tie risk + WIDE PIT diff')
    w('#' * 100)

    for name, (key, vals) in TABLES.items():
        w()
        w('=' * 100)
        w('%s   FULL_KEY=%s' % (name, key))
        w('=' * 100)

        de, dhave = load('D', name, key, vals)
        ee, ehave = load('E', name, key, vals)
        if de is None or ee is None:
            w('  MISSING D=%s E=%s' % (de is not None, ee is not None))
            continue
        vc = [c for c in vals if c in dhave and c in ehave]

        # ---------------- coverage
        w('  COVERAGE')
        cov = {}
        for side, d, hv in (('D', de, dhave), ('E', ee, ehave)):
            # Use the REAL parquet schema, not d.columns: load() injects 'NA'
            # sentinel columns for absent dates, which would mask the truth.
            a_src = 'ann_date' if 'ann_date' in hv else (
                'change_date' if 'change_date' in hv else None)
            p_src = 'end_date' if 'end_date' in hv else (
                'change_date' if 'change_date' in hv else None)
            if a_src is None:
                w('    [%s] rows=%-8d NO DATE COLUMN (schema=%s)'
                  % (side, len(d), hv[:6]))
                cov[side] = ('NA', 'NA')
                continue
            ann = canon_date(d[a_src])
            per = canon_date(d[p_src]) if p_src else ann
            a = ann[ann != 'NA']
            cov[side] = (a.min() if len(a) else 'NA',
                         a.max() if len(a) else 'NA')
            w('    [%s] rows=%-8d ann(%s) min=%s max=%s   period max=%s'
              % (side, len(d), a_src,
                 cov[side][0], cov[side][1], per.max()))
        dcut = cov['D'][1]
        if not isinstance(dcut, str) or dcut == 'NA':
            w('    (no usable D ann_date cutoff -> skip PIT verdict split)')
            dcut = None

        # ---------------- real reader-tie risk
        w('  READER-TIE RISK (same report period, several distinct ann_date)')
        for side, d, hv in (('D', de, dhave), ('E', ee, ehave)):
            pk = period_key(d, key)
            tie_src = ('ann_date' if 'ann_date' in hv else
                       ('change_date' if 'change_date' in hv else None))
            if tie_src is None:
                continue
            annc = canon_date(d[tie_src])
            nun = annc.groupby(pk.values, sort=False).nunique()
            multi = int((nun > 1).sum())
            # how many rows sit in those ambiguous periods
            amb = nun[nun > 1].index
            sel = pk.isin(set(amb))
            w('    [%s] ambiguous periods=%-7d (%.2f%% of %d periods) '
              'rows involved=%d'
              % (side, multi,
                 100.0 * multi / max(len(nun), 1), len(nun), int(sel.sum())))

        # ---------------- wide PIT comparison
        d2 = rule_r2(de, key, vc)
        e2 = rule_r2(ee, key, vc)
        codes = sorted(set(d2['ts_code'].astype(str)) |
                       set(e2['ts_code'].astype(str)))
        rng = np.random.RandomState(20260904)
        pick = list(rng.permutation(len(codes))[:150])
        codes = [codes[i] for i in sorted(pick)]

        ASOF = ['20120630', '20151231', '20171229', '20191231', '20211231',
                '20221230', '20231229', '20240630', '20250630', '20260630',
                '20260831']
        ann_col = ('ann_date' if 'ann_date' in key else 'change_date')
        pcol = ('end_date'
                if ('end_date' in dhave or 'end_date' in ehave)
                else 'change_date')

        dsub = {c: g for c, g in d2.groupby('ts_code', sort=False)}
        esub = {c: g for c, g in e2.groupby('ts_code', sort=False)}
        res = {}
        for a in ASOF:
            n = same = 0
            e_has_more = 0
            real = 0
            samples = []
            for c in codes:
                rd = pit_pick(dsub[c], ann_col, a) if c in dsub else None
                re_ = pit_pick(esub[c], ann_col, a) if c in esub else None
                if rd is None and re_ is None:
                    continue
                n += 1
                if rd is None or re_ is None:
                    e_has_more += 1
                    continue
                bd = canon_date(pd.Series([rd[pcol]])).iloc[0]
                be = canon_date(pd.Series([re_[pcol]])).iloc[0]
                bad = []
                if bd != be:
                    bad.append('end %s|%s' % (bd, be))
                for col in vc:
                    x, y = rd[col], re_[col]
                    if pd.isna(x) and pd.isna(y):
                        continue
                    try:
                        if abs(float(x) - float(y)) > 1e-9:
                            bad.append('%s %s|%s' % (col, x, y))
                    except Exception:
                        if str(x) != str(y):
                            bad.append('%s %s|%s' % (col, x, y))
                if bad:
                    real += 1
                    if len(samples) < 3:
                        samples.append('%s %s' % (c, bad[:2]))
                else:
                    same += 1
            res[a] = (n, same, e_has_more, real, samples)
            flag = 'FRESHNESS-ONLY' if (real == 0 and e_has_more) else ''
            w('    asof=%s pairs=%-5d identical=%-5d E-has-extra=%-4d '
              'REAL-DIFF=%-4d %s'
              % (a, n, same, e_has_more, real, flag))
            for s_ in samples:
                w('            %s' % s_)

        w('  PIT VERDICT (periods <= D cutoff %s):' % dcut)
        bad_ct = 0
        for a in ASOF:
            if dcut is not None and a <= dcut and res[a][3]:
                bad_ct += res[a][3]
                w('      !! asof=%s REAL-DIFF=%d' % (a, res[a][3]))
        if dcut is None:
            w('      N/A - table has no usable D ann_date cutoff')
        elif bad_ct == 0:
            w('      OK - zero value-level differences on every asof <= D '
              'ann_date cutoff')


if __name__ == '__main__':
    main()
    OUT.close()
