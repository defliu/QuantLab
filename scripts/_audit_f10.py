# -*- coding: utf-8 -*-
"""
Decisive test for fina_indicator: same vendor with different column fill
allocation, or a genuinely different/broken product?

For every near-empty numeric column c in E:
  1. rows where D has non-null c but E has null c  -> "DED" (would-be loss)
  2. in those same rows, does E carry D's value in a SIBLING column? -> migration
  3. among co-non-null rows, do values agree numerically?
"""
import sys
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8')

DP = r'D:\astock\finance\fina_indicator.parquet'
EP = r'E:\temp\行情数据\fina_indicator.parquet'

KEY = ['ts_code', 'end_date', 'ann_date']
DATE_COLS = ['end_date', 'ann_date']


def read(p):
    t = pq.read_table(p)
    cols = [c for c in t.column_names if not c.startswith('__index_level_')]
    return t.select(cols).to_pandas()


def canon_date(s):
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d').fillna('NA').astype(str)
    t = s.astype(str).str.strip()
    t = t.where(~t.isin(('nan', 'NaT', 'None', '', '--')), 'NA')
    g = t.str.split(' ').str[0].str.replace(r'\D', '', regex=True)
    o = g.where(g.str.len() == 8, 'NA')
    need = (o == 'NA') & (t != 'NA')
    if need.any():
        p = pd.to_datetime(t.where(need), errors='coerce', format='mixed')
        o = o.where(~need, p.dt.strftime('%Y%m%d').fillna('NA'))
    return o.fillna('NA').astype(str)


def canon_code(s):
    return s.astype(str).str.strip().replace({'nan': 'NA', 'NaT': 'NA'})


d = read(DP)
e = read(EP)
print('D shape', d.shape, ' E shape', e.shape)

for c in DATE_COLS:
    d[c] = canon_date(d[c])
    e[c] = canon_date(e[c])
d['ts_code'] = canon_code(d['ts_code'])
e['ts_code'] = canon_code(e['ts_code'])

print('sample D key:', d[KEY].iloc[0].tolist())
print('sample E key:', e[KEY].iloc[0].tolist())

only_d = sorted(set(d.columns) - set(e.columns))
only_e = sorted(set(e.columns) - set(d.columns))
print('D-only cols', len(only_d), only_d[:10])
print('E-only cols', len(only_e), only_e[:10])

dk = d[KEY].drop_duplicates()
ek = e[KEY].drop_duplicates()
shared = dk.merge(ek, on=KEY, how='inner')
print('D keys', len(dk), '| E keys', len(ek), '| shared keys', len(shared))

e1 = e.drop_duplicates(subset=KEY, keep='first')
dd = d.drop_duplicates(subset=KEY, keep='first').set_index(KEY)
ee = e1.set_index(KEY)
idx = dd.index.intersection(ee.index)
dd = dd.loc[idx]
ee = ee.loc[idx]
print('aligned rows', len(dd))

cols = [c for c in dd.columns if c in ee.columns]
val_cols = [c for c in cols if pd.api.types.is_numeric_dtype(dd[c])]
print('common cols', len(cols), '| numeric common', len(val_cols))

n = len(ee)
near_empty = [c for c in val_cols if int(ee[c].notna().sum()) / n < 0.02]
print()
print('E near-empty numeric cols (<2%% non-null): %d' % len(near_empty))


def agree_stat(a, b):
    a = pd.to_numeric(a, errors='coerce')
    b = pd.to_numeric(b, errors='coerce')
    both = a.notna() & b.notna()
    k = int(both.sum())
    if k == 0:
        return '0 co-null'
    diff = (a - b).abs()
    scale = a.abs().clip(lower=1e-9)
    rel = (diff / scale).where(scale > 1e-6, diff)
    return '%d co-null/%d bad' % (k, int((rel > 1e-6).sum()))


rows = []
for c in near_empty:
    ded_mask = (dd[c].notna() & ee[c].isna()).to_numpy()
    ded = int(ded_mask.sum())
    mig = 0
    mig_col = ''
    if ded > 0:
        dv = pd.to_numeric(dd[c].to_numpy()[ded_mask], errors='coerce')
        tol = (pd.Series(dv).abs() * 1e-6).clip(lower=1e-6).to_numpy()
        sub = ee.loc[ded_mask, val_cols].apply(pd.to_numeric, errors='coerce')
        for s in val_cols:
            if s == c:
                continue
            close = (sub[s].to_numpy() - dv)
            m = int((np.abs(close) <= tol).sum())
            if m > mig:
                mig = m
                mig_col = s
    rows.append(dict(col=c, d_nn=int(dd[c].notna().sum()),
                     e_nn=int(ee[c].notna().sum()), ded=ded,
                     mig=mig, mig_col=mig_col, agree=agree_stat(dd[c], ee[c])))

rep = pd.DataFrame(rows)
rep['mig_ratio'] = (rep['mig'] / rep['ded'].replace(0, pd.NA)).astype(float).round(3)
pd.set_option('display.width', 260)
pd.set_option('display.max_rows', 300)
print()
print(rep.to_string(index=False))
print()
print('near-empty cols with DED>0       :', int((rep['ded'] > 0).sum()))
print('near-empty cols with mig_ratio>.5 :', int((rep['mig_ratio'] > 0.5).sum()))
print('total DED cells (aligned)        :', int(rep['ded'].sum()))
print('total migrated cells             :', int(rep['mig'].sum()))
rep.to_csv(r'D:\QuantLab\scripts\_audit_f10_rep.csv', index=False, encoding='utf-8-sig')
