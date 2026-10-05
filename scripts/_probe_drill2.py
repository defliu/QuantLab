# coding: utf-8
"""Drill 2: are the historical PIT diffs caused by RULE R2 (wrong version pick)
or by E genuinely rewriting history?

Method: apply ONE deterministic PIT picker to three inputs and compare:
    A) D raw        (no dedup at all)
    B) E raw        (no dedup at all)
    C) E after R2   (dedup: flag==1 first, newest ann, ...)

  - If A == B  and A != C  -> R2 is the culprit. E did NOT rewrite history;
                               our own dedup rule picks a different variant.
  - If A != B               -> E genuinely carries a different value for the
                               same report period -> real restatement / hole.

The picker mirrors the production reader (f_ann_date preferred, ann_date <= asof,
max report period) but with an explicit deterministic tie-break instead of
idxmax()'s first-row behaviour, so the comparison is reproducible.
"""
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

D = r'D:/astock/finance'
E = r'E:/temp/行情数据'
OUTPATH = r'D:/QuantLab/browse/_finance_drill2.txt'
SEP = '\x1f'

TABLES = {
    'fina_indicator': (['ts_code', 'end_date', 'ann_date'],
                       ['eps', 'roe', 'bps']),
    'balancesheet': (['ts_code', 'end_date', 'report_type', 'ann_date'],
                     ['total_assets', 'total_liab']),
    'income': (['ts_code', 'end_date', 'report_type', 'ann_date'],
               ['revenue', 'n_income_attr_p']),
    'cashflow': (['ts_code', 'end_date', 'report_type', 'ann_date'],
                 ['n_cashflow_act']),
}

# (table, code, asof) taken verbatim from the wide probe's REAL-DIFF samples
CASES = [
    ('fina_indicator', '002781.SZ', '20151231'),
    ('fina_indicator', '002781.SZ', '20171229'),
    ('fina_indicator', '600105.SH', '20171229'),
    ('fina_indicator', '600105.SH', '20191231'),
    ('fina_indicator', '002569.SZ', '20221230'),
    ('fina_indicator', '600576.SH', '20221230'),
    ('fina_indicator', '300205.SZ', '20250630'),
    ('fina_indicator', '603169.SH', '20250630'),
    ('balancesheet', '920300.BJ', '20191231'),
    ('balancesheet', '874025.BJ', '20260630'),
    ('balancesheet', '874287.BJ', '20260630'),
    ('income', '874159.BJ', '20260630'),
    ('cashflow', '688388.SH', '20221230'),
    ('cashflow', '831406.BJ', '20231229'),
    ('cashflow', '831928.BJ', '20250630'),
    ('cashflow', '832513.BJ', '20250630'),
    ('cashflow', '600893.SH', '20260630'),
    ('cashflow', '601099.SH', '20260630'),
]

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


def group_key(d, key):
    parts = []
    for c in key:
        parts.append(canon_date(d[c]) if c in ('end_date', 'ann_date',
                                               'f_ann_date',
                                               'change_date')
                     else d[c].astype(str).str.strip())
    g = parts[0]
    for p in parts[1:]:
        g = g.str.cat(p, sep=SEP)
    return g.astype(str)


def load(side, name, key, vals):
    root = D if side == 'D' else E
    fp = os.path.join(root, name + '.parquet')
    if not os.path.exists(fp):
        return None
    have = list(pq.ParquetFile(fp).schema_arrow.names)
    want = [c for c in dict.fromkeys(list(key) + list(vals) +
                                     ['update_flag', 'f_ann_date'])
            if c in have]
    return pd.read_parquet(fp, columns=want)


def rule_r2(d, key, val_cols):
    g = group_key(d, key)
    if 'update_flag' not in d.columns:
        d = d.assign(update_flag='NA')
    if 'f_ann_date' not in d.columns:
        d = d.assign(f_ann_date='NA')
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
    keep = np.sort(meta.drop_duplicates('g', keep='first')['ridx'].values)
    return d.iloc[keep]


def pick(sub, asof, val_cols):
    """Production-reader-equivalent PIT pick, deterministic tie-break."""
    if sub is None or len(sub) == 0:
        return None
    ann_col = 'f_ann_date' if 'f_ann_date' in sub.columns else 'ann_date'
    if ann_col not in sub.columns:
        return None
    ann = canon_date(sub[ann_col])
    vis = sub[ann.le(asof)]
    if vis.empty:
        return None
    per = pd.to_datetime(canon_date(vis['end_date']), format='%Y%m%d',
                         errors='coerce').fillna(pd.Timestamp.min)
    vcols = [c for c in val_cols if c in vis.columns]
    if vcols:
        fp = pd.util.hash_pandas_object(vis[vcols].astype(object),
                                        index=False).astype('uint64')
    else:
        fp = pd.Series([0] * len(vis), index=vis.index)
    vv = vis.assign(_p=per.values, _a=ann.loc[vis.index].values,
                    _h=fp.astype('uint64').values)
    vv = vv.sort_values(['_p', '_a', '_h'],
                        ascending=[False, False, True])
    return vv.iloc[0]


def sig(row, val_cols):
    if row is None:
        return 'NONE'
    parts = [str(row.get('end_date'))]
    for c in val_cols:
        if c in row.index:
            v = row[c]
            try:
                parts.append('%s=%.6f' % (c, float(v)))
            except Exception:
                parts.append('%s=%s' % (c, v))
    return ' '.join(parts)


def main():
    w('#' * 100)
    w('# DRILL 2 - R2 artefact vs genuine E-side rewrite')
    w('#' * 100)

    tally = {'R2_ARTEFACT': 0, 'GENUINE_REWRITE': 0, 'D_RICHER': 0,
             'NO_DIFF': 0, 'INCONCLUSIVE': 0}

    for name, code, asof in CASES:
        key, vals = TABLES[name]
        dd = load('D', name, key, vals)
        ee = load('E', name, key, vals)
        w()
        w('=' * 100)
        w('%s  %s  asof=%s' % (name, code, asof))
        w('=' * 100)

        dsub = dd[dd['ts_code'].astype(str) == code]
        esub = ee[ee['ts_code'].astype(str) == code]
        e_r2 = rule_r2(esub, key, vals)

        ra = pick(dsub, asof, vals)          # A: D raw
        rb = pick(esub, asof, vals)          # B: E raw
        rc = pick(e_r2, asof, vals)          # C: E after R2

        sa, sb, sc = sig(ra, vals), sig(rb, vals), sig(rc, vals)
        w('  A D-raw   : %s' % sa)
        w('  B E-raw   : %s' % sb)
        w('  C E-afterR2: %s' % sc)

        if sa == sb and sa != sc:
            verdict = 'R2_ARTEFACT'
            note = ('E raw == D raw -> E did NOT rewrite history; '
                    'R2 flag-priority picked a different variant')
        elif sa != sb and sa == sc:
            verdict = 'GENUINE_REWRITE'
            note = ('E raw != D raw even before dedup -> E carries a '
                    'different value for the same report period')
        elif sa == sb and sa == sc:
            verdict = 'NO_DIFF'
            note = 'all three agree - the wide-probe diff did not reproduce'
        elif ra is not None and rb is None:
            verdict = 'D_RICHER'
            note = 'D has a visible row where E has none'
        else:
            verdict = 'INCONCLUSIVE'
            note = 'mixed pattern - inspect the row dump below'
        tally[verdict] += 1
        w('  >> %s : %s' % (verdict, note))

        # dump the rows that matter: everything visible at asof, plus the
        # winning report period on each side
        periods = set()
        for r in (ra, rb, rc):
            if r is not None:
                periods.add(str(r.get('end_date')))
        w('  ROWS visible at asof (report periods %s):' % sorted(periods))
        show = ['end_date', 'ann_date', 'f_ann_date', 'update_flag'] + \
            [c for c in vals if c in esub.columns]
        for tag, sub in (('D', dsub), ('E', esub)):
            if sub is None or len(sub) == 0:
                w('    [%s] no rows' % tag)
                continue
            ac = 'f_ann_date' if 'f_ann_date' in sub.columns else 'ann_date'
            av = canon_date(sub[ac]) if ac in sub.columns else None
            sub2 = sub[av.le(asof)] if av is not None else sub
            cols = [c for c in show if c in sub2.columns]
            if len(sub2) == 0:
                w('    [%s] no visible rows' % tag)
                continue
            body = sub2[cols].copy()
            body['_annvis'] = av.loc[sub2.index].values if av is not None \
                else ''
            body = body.sort_values(['end_date', '_annvis'])
            w('    [%s] visible rows=%d' % (tag, len(sub2)))
            for line in body.to_string(index=False).splitlines():
                w('      %s' % line)

    w()
    w('#' * 100)
    w('# TALLY')
    for k in sorted(tally):
        w('  %-16s %d' % (k, tally[k]))
    w('#' * 100)
    OUT.close()


if __name__ == '__main__':
    main()