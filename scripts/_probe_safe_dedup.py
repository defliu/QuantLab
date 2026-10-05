# coding: utf-8
"""Finance dedup safety probe (D:/astock/finance  vs  E:/temp/行情数据).

Design notes (fixes applied after the first buggy runs):

1. SCHEMA-DRIVEN column loading. Some tables have no `update_flag`
   (dividend / express / share_change_event) -> synthetic column is ADDED
   and KEPT (the old code re-sliced `d[want]` and silently dropped it).

2. CANONICAL KEY. Every key column is stringified; date columns are
   normalized to 'YYYYMMDD'. D stores ISO timestamps
   ('2023-12-31 00:00:00'), E stores compact strings ('20231231');
   comparing raw tuples produced thousands of fake "D-only" keys.

3. GROUP KEY AS ONE STRING. `'\x1f'.join(...)` -> groupby on a plain
   object column. This removes every categorical-groupby cartesian-expansion
   risk and every groupby(dropna=True) key-loss risk at once.

4. FULLY DETERMINISTIC tie-break, independent of parquet row order:
   flag(1 first) -> ann_date desc -> f_ann_date desc -> value-hash asc
   -> original index asc (last one only to make sort total).

Questions answered:
  Q1  Does the naive `update_flag == '1'` filter DROP whole announcements?
  Q2  Does the safe rule give 0 lost FULL_KEYs / 0 duplicates / 100% keep?
  Q3  Is D(safe-deduped) a subset of E(safe-deduped) on canonical keys?
  Q4  On sample (code, asof) pairs, does the PIT-visible winner carry the
      same field values on D and E?  (backtest reproducibility)
  Q5  Production-reader risk: how often do tied rows exist, and do tied rows
      disagree on values?  -> is `idxmax()` non-deterministic in practice?
"""
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

D = r'D:/astock/finance'
E = r'E:/temp/行情数据'
OUTPATH = r'D:/QuantLab/browse/_finance_safededup.txt'

SEP = '\x1f'
DATE_COLS = ('end_date', 'ann_date', 'change_date', 'f_ann_date')

TABLES = {
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
    'share_change_event': ['ts_code', 'change_date', 'change_reason'],
}

VAL = {
    'fina_indicator': ['eps', 'roe', 'gross_margin', 'net_margin', 'bps'],
    'balancesheet': ['total_assets', 'total_liab',
                     'total_hldr_eqy_exc_min_int'],
    'income': ['revenue', 'n_income_attr_p', 'operate_profit'],
    'cashflow': ['n_cashflow_act', 'n_cashflow_inv_act',
                 'n_cash_flows_fnc_act'],
    'forecast': ['p_change_min', 'net_profit_min', 'type'],
    'dividend': ['cash_div', 'cash_div_tax', 'stk_div'],
    'express': ['revenue', 'n_income_attr_p'],
    'share_change_event': ['change_ratio', 'change_amount'],
}

OUT = open(OUTPATH, 'w', encoding='utf-8')


def w(s=''):
    print(s)
    OUT.write(s + '\n')


# ---------------------------------------------------------------- normalizers
def norm_flag(s):
    """'1'/'1.0' -> '1'; '0'/'0.0' -> '0'; anything else -> 'NA'."""
    t = s.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    return t.where(t.isin(['0', '1']), 'NA')


def canon_date(s):
    """Any date-ish representation -> 'YYYYMMDD' string, 'NA' if unparsable."""
    t = s.astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
    t = t.where(~t.isin(['nan', 'NaT', 'None', '']), 'NA')
    compact = t.str.replace('-', '', regex=False).str.replace('/', '',
                                                              regex=False)
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
    """One deterministic string key per row; NaN-safe, type-safe."""
    parts = [canon_col(d[c], c) for c in key]
    g = parts[0]
    for p in parts[1:]:
        g = g.str.cat(p, sep=SEP)
    return g.astype(str)


def key_tuples(gset):
    return sorted(gset, key=lambda s: tuple(s.split(SEP)))


# --------------------------------------------------------------------- load
def load(side, name, key):
    root = D if side == 'D' else E
    fp = os.path.join(root, name + '.parquet')
    if not os.path.exists(fp):
        return None, None
    have = list(pq.ParquetFile(fp).schema_arrow.names)
    want = [c for c in dict.fromkeys(key + VAL[name] + ['update_flag',
                                                       'f_ann_date'])
            if c in have]
    d = pd.read_parquet(fp, columns=want)
    for c in ('end_date', 'ann_date', 'change_date', 'f_ann_date',
              'update_flag'):
        if c not in d.columns:
            d[c] = 'NA'
    return d, have


# --------------------------------------------------------------------- rules
def rule_r1(d, key):
    """Naive: keep only update_flag == '1'."""
    return d[norm_flag(d['update_flag']) == '1']


def rule_r2(d, key, val_cols):
    """flag==1 first; then newest announcement; then value-hash; then index."""
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
    return d.iloc[sort_back(keep, len(d))]


def sort_back(keep, n):
    order = np.argsort(keep)
    return np.asarray(keep)[order]


# ---------------------------------------------------------------- PIT reader
def pit_pick(sub, ann_col, asof_str):
    """Deterministic PIT pick: ann<=asof, max end_date, then max ann_date,
    then value-hash -- i.e. what the reader *should* do."""
    ann = canon_date(sub[ann_col])
    vis = sub[ann.le(asof_str)]
    if vis.empty:
        return None
    end = pd.to_datetime(canon_date(vis['end_date']), format='%Y%m%d',
                         errors='coerce')
    o = end.fillna(pd.Timestamp.min)
    fp = pd.util.hash_pandas_object(vis.astype(object), index=False)
    vis = vis.assign(_o=o.values, _a=ann.loc[vis.index].values,
                     _h=fp.astype('uint64').values)
    vis = vis.sort_values(['_o', '_a', '_h'],
                          ascending=[False, False, True])
    return vis.iloc[0]


def main():
    w('#' * 100)
    w('# Q1/Q2  dedup safety      Q3  D subset of E      Q5  reader tie risk')
    w('#' * 100)

    summary = []
    for name, key in TABLES.items():
        w()
        w('=' * 100)
        w('%-20s FULL_KEY = %s' % (name, key))
        w('=' * 100)

        de, dhave = load('D', name, key)
        ee, ehave = load('E', name, key)
        if de is None or ee is None:
            w('  MISSING  D=%s E=%s' % (de is not None, ee is not None))
            continue
        vcols = [c for c in VAL[name] if c in dhave and c in ehave]

        for side, d in (('D', de), ('E', ee)):
            n_raw = len(d)
            g = group_key(d, key)
            n_fk = g.nunique()

            r1 = rule_r1(d, key)
            r1_g = group_key(r1, key)
            n1 = r1_g.nunique()
            lost = n_fk - n1
            lost_rows = n_raw - len(r1)

            r2 = rule_r2(d, key, vcols)
            g2 = group_key(r2, key)
            n2 = g2.nunique()

            w('  [%s] rows=%-8d FULL_KEYs=%-8d  update_flag in schema=%s'
              % (side, n_raw, n_fk,
                 'update_flag' in (dhave if side == 'D' else ehave)))
            w('       R1 keep flag==1   : rows=%-8d FULL_KEYs=%-8d '
              'LOST-FK=%-7d (%6.2f%%)  rows dropped=%d'
              % (len(r1), n1, lost, 100.0 * lost / n_fk, lost_rows))
            w('       R2 safe rule       : rows=%-8d FULL_KEYs=%-8d '
              'LOST-FK=%-7d (%6.2f%%)'
              % (len(r2), n2, n_fk - n2, 100.0 * (n_fk - n2) / n_fk))
            summary.append((name, side, n_raw, n_fk, lost, len(r2), n2))

        # ---- Q3 canonical subset test
        dk = set(group_key(rule_r2(de, key, vcols), key))
        ek = set(group_key(rule_r2(ee, key, vcols), key))
        donly = dk - ek
        eonly = ek - dk
        w('  Q3  D_R2=%-8d E_R2=%-8d  D-only=%-6d E-only=%-6d  D subset of E: %s'
          % (len(dk), len(ek), len(donly), len(eonly), not donly))
        for m in key_tuples(donly)[:4]:
            w('        D-only  %s' % (m,))
        for m in key_tuples(eonly)[:2]:
            w('        E-only  %s' % (m,))

        # ---- Q5 reader tie risk (full table, not sampled)
        for side, d in (('D', de), ('E', ee)):
            g = group_key(d, key)
            ann = canon_date(d['ann_date'])
            end = canon_date(d['end_date'])
            ts = pd.DataFrame({'g': g.values, 'ann': ann.values,
                               'end': end.values})
            sz = ts.groupby(['g'], sort=False).size()
            multi = sz[sz > 1]
            # do the tied rows disagree on the value columns?
            disagree = 0
            if len(multi):
                v = d[vcols].astype(object) if vcols else None
                nun = v.groupby(g.values, sort=False).nunique() if vcols \
                    else None
                if nun is not None:
                    disagree = int((nun.max(axis=1) > 1).sum())
            # same end_date, different ann_date -> reader may pick stale row
            tie_end = int(ts.groupby(['g', 'end'], sort=False)['ann']
                          .nunique().gt(1).sum())
            w('  Q5  [%s] FULL_KEYs with >1 row = %-7d ; of those value-'
              'disagreeing = %-7d (%.1f%%) ; same end_date but different '
              'ann_date = %d'
              % (side, len(multi), disagree,
                 100.0 * disagree / len(multi) if len(multi) else 0.0,
                 tie_end))

        # ---- Q4 PIT-visible winner comparison
        w('  Q4  PIT winner field comparison (8 codes x 6 asof)')
        ASOF = ['20150630', '20181231', '20211231', '20231229', '20250630',
                '20260831']
        CODES = ['000001.SZ', '000002.SZ', '600000.SH', '600519.SH',
                 '300750.SZ', '002594.SZ', '601318.SH', '000651.SZ']
        ann_col = 'ann_date' if 'ann_date' in key else 'change_date'
        d2 = rule_r2(de, key, vcols)
        e2 = rule_r2(ee, key, vcols)
        tot = same = 0
        diffs = []
        for code in CODES:
            dsub = d2[d2['ts_code'] == code]
            esub = e2[e2['ts_code'] == code]
            for a in ASOF:
                rd = pit_pick(dsub, ann_col, a) if len(dsub) else None
                re_ = pit_pick(esub, ann_col, a) if len(esub) else None
                if rd is None and re_ is None:
                    continue
                tot += 1
                if rd is None or re_ is None:
                    diffs.append('%s@%s MISSING_SIDE D=%s E=%s'
                                 % (code, a,
                                    None if rd is None else
                                    canon_date(pd.Series([rd['end_date']]))
                                    .iloc[0],
                                    None if re_ is None else
                                    canon_date(pd.Series([re_['end_date']]))
                                    .iloc[0]))
                    continue
                bad = []
                de_ = canon_date(pd.Series([rd['end_date']])).iloc[0]
                ee_ = canon_date(pd.Series([re_['end_date']])).iloc[0]
                if de_ != ee_:
                    bad.append('end %s|%s' % (de_, ee_))
                for c in vcols:
                    x, y = rd[c], re_[c]
                    if pd.isna(x) and pd.isna(y):
                        continue
                    try:
                        if abs(float(x) - float(y)) > 1e-9:
                            bad.append('%s %s|%s' % (c, x, y))
                    except Exception:
                        if str(x) != str(y):
                            bad.append('%s %s|%s' % (c, x, y))
                if bad:
                    diffs.append('%s@%s %s' % (code, a, bad[:3]))
                else:
                    same += 1
        w('       comparable=%d  identical=%d  differing=%d'
          % (tot, same, tot - same))
        for d_ in diffs[:6]:
            w('       DIFF %s' % d_)

    w()
    w('=' * 100)
    w('SUMMARY  table / side / rows / FULL_KEYs / R1-lost-FK / R2-rows / '
      'R2-FULL_KEYs')
    w('=' * 100)
    for r in summary:
        w('  %-20s %s  rows=%-8d FK=%-8d R1lost=%-7d R2rows=%-8d R2FK=%-8d'
          % (r[0], r[1], r[2], r[3], r[4], r[5], r[6]))


if __name__ == '__main__':
    main()
    OUT.close()
