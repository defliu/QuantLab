# -*- coding: utf-8 -*-
"""跨财务表判定（按业务键对齐）：损失是"历史回填不全"还是"真口径差异"。"""
import sys
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8')

FULL_KEYS = {
    'income':       ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow':     ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'fina_ind':     ['ts_code', 'end_date', 'ann_date'],
    'forecast':     ['ts_code', 'end_date', 'ann_date', 'type'],
    'express':      ['ts_code', 'end_date', 'ann_date'],
    'dividend':     ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'share_chg':    ['ts_code', 'change_date', 'change_reason'],
}
PAIRS = [
    ('income',       r'D:\astock\finance\income.parquet',       r'E:\temp\行情数据\income.parquet'),
    ('balancesheet', r'D:\astock\finance\balancesheet.parquet', r'E:\temp\行情数据\balancesheet.parquet'),
    ('cashflow',     r'D:\astock\finance\cashflow.parquet',     r'E:\temp\行情数据\cashflow.parquet'),
    ('fina_ind',     r'D:\astock\finance\fina_indicator.parquet', r'E:\temp\行情数据\fina_indicator.parquet'),
    ('forecast',     r'D:\astock\finance\forecast.parquet',     r'E:\temp\行情数据\forecast.parquet'),
    ('express',      r'D:\astock\finance\express.parquet',      r'E:\temp\行情数据\express.parquet'),
    ('dividend',     r'D:\astock\finance\dividend.parquet',     r'E:\temp\行情数据\dividend.parquet'),
    ('share_chg',    r'D:\astock\finance\share_change_event.parquet', r'E:\temp\行情数据\share_change_event.parquet'),
]
SKIP = set(['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'report_type',
            'comp_type', 'end_type', 'div_proc', 'change_date',
            'change_reason', 'type', 'update_flag'])


def read(p):
    t = pq.read_table(p)
    cols = [c for c in t.column_names if not c.startswith('__index_level_')]
    return t.select(cols).to_pandas()


def canon(s):
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


summary = []
for name, dp, ep in PAIRS:
    d = read(dp)
    e = read(ep)
    keys = FULL_KEYS[name]
    dcode = d['ts_code'].astype(str).str.strip()
    ecode = e['ts_code'].astype(str).str.strip()
    d = d.assign(ts_code=dcode)
    e = e.assign(ts_code=ecode)
    dcols = [c for c in keys if c in d.columns]
    for c in dcols:
        if c not in ('ts_code',):
            d[c] = canon(d[c])
            if c in e.columns:
                e[c] = canon(e[c])
    keys = [c for c in dcols if c in e.columns]

    nd, ne = len(d), len(e)
    val_cols = [c for c in d.columns
                if c not in SKIP and c in e.columns
                and pd.api.types.is_numeric_dtype(d[c])]

    dd = d.drop_duplicates(subset=keys, keep='first').set_index(keys)
    ee = e.drop_duplicates(subset=keys, keep='first').set_index(keys)
    idx = dd.index.intersection(ee.index)
    A = dd.loc[idx]
    B = ee.loc[idx]
    d_only = len(dd) - len(idx)

    ysrc = 'ann_date'
    if ysrc in A.columns:
        dyear = A[ysrc].astype(str).str[:4].to_numpy()
    else:
        lv = A.index.get_level_values(ysrc) if ysrc in A.index.names else None
        dyear = (pd.Series(lv).astype(str).str[:4].to_numpy()
                 if lv is not None else None)

    loss_by_year = {}
    loss_cols = 0
    tot = 0
    for c in val_cols:
        m = (A[c].notna() & B[c].isna()).to_numpy()
        k = int(m.sum())
        if k == 0:
            continue
        loss_cols += 1
        tot += k
        if dyear is not None:
            yy = dyear[m]
            for y in pd.unique(yy):
                loss_by_year[y] = loss_by_year.get(y, 0) + int((yy == y).sum())

    recent = sum(v for y, v in loss_by_year.items() if y >= '2024')
    old = tot - recent
    summary.append((name, nd, ne, len(idx), d_only, tot, loss_cols, recent, old, loss_by_year))

    print('=' * 74)
    print('%s  D %d / E %d  对齐后 %d 行, D独有 %d 行' % (name, nd, ne, len(idx), d_only))
    print('  D有E空单元格 : %d  (涉及 %d 列)' % (tot, loss_cols))
    if tot:
        print('    2024+ : %d (%.1f%%)   <=2023 : %d (%.1f%%)'
              % (recent, 100.0 * recent / tot, old, 100.0 * old / tot))
        top = sorted(loss_by_year.items(), key=lambda x: -x[1])[:5]
        print('    损失最多年份:', ', '.join('%s=%d' % t for t in top))

print()
print('=' * 74)
print('汇总：财务表值损失（按业务键对齐口径）')
print('=' * 74)
print('%-14s %10s %10s %8s %12s %10s' % ('表', 'D行', 'E行', 'D独有', 'D有E空格', '涉列'))
gt = 0
for r in summary:
    gt += r[5]
    print('%-14s %10d %10d %8d %12d %10d' % (r[0], r[1], r[2], r[4], r[5], r[6]))
print('合计 D有E空单元格: %d' % gt)
