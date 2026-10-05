# -*- coding: utf-8 -*-
"""income/balancesheet 大损失列：验证是「同族列改名」还是「真丢历史」。"""
import sys
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8')

FULL_KEYS = {
    'income':       ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
}
PAIRS = [
    ('income',       r'D:\astock\finance\income.parquet',
     r'E:\temp\行情数据\income.parquet'),
    ('balancesheet', r'D:\astock\finance\balancesheet.parquet',
     r'E:\temp\行情数据\balancesheet.parquet'),
]
SKIP = set(['ts_code', 'end_date', 'ann_date', 'f_ann_date', 'report_type',
            'comp_type', 'end_type', 'update_flag'])


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


rows = []
for name, dp, ep in PAIRS:
    d = read(dp)
    e = read(ep)
    keys = FULL_KEYS[name]
    d = d.assign(ts_code=d['ts_code'].astype(str).str.strip())
    e = e.assign(ts_code=e['ts_code'].astype(str).str.strip())
    for c in keys:
        if c != 'ts_code':
            d[c] = canon(d[c])
            if c in e.columns:
                e[c] = canon(e[c])
    keys = [c for c in keys if c in e.columns]
    val_cols = [c for c in d.columns
                if c not in SKIP and c in e.columns
                and pd.api.types.is_numeric_dtype(d[c])]

    dd = d.drop_duplicates(subset=keys, keep='first').set_index(keys)
    ee = e.drop_duplicates(subset=keys, keep='first').set_index(keys)
    idx = dd.index.intersection(ee.index)
    A = dd.loc[idx]
    B = ee.loc[idx]
    n = len(A)
    print('=' * 78)
    print('%s  对齐 %d 行, 数值列 %d' % (name, n, len(val_cols)))
    print('-' * 78)

    # 找损失最大的 8 列
    loss = {}
    for c in val_cols:
        m = (A[c].notna() & B[c].isna()).to_numpy()
        k = int(m.sum())
        if k:
            loss[c] = k
    top = sorted(loss.items(), key=lambda x: -x[1])[:8]

    for c, k in top:
        m = (A[c].notna() & B[c].isna()).to_numpy()
        dv = pd.to_numeric(A[c].to_numpy()[m], errors='coerce')
        tol = np.maximum(np.abs(dv) * 1e-6, 1e-6)
        sub = B.loc[m, val_cols].apply(pd.to_numeric, errors='coerce')

        # 同名共同非空处数值是否一致（口径一致性锚点）
        both = (A[c].notna() & B[c].notna()).to_numpy()
        n_both = int(both.sum())
        if n_both:
            av = np.asarray(pd.to_numeric(A[c].to_numpy()[both], errors='coerce'),
                            dtype='float64')
            bv = np.asarray(pd.to_numeric(B[c].to_numpy()[both], errors='coerce'),
                            dtype='float64')
            ok = np.isfinite(av) & np.isfinite(bv)
            diff = np.abs(av[ok] - bv[ok])
            rel = diff / np.maximum(np.abs(av[ok]), 1e-9)
            hit_mask = (diff <= 1e-6) | (rel <= 1e-6)
            agree = int(hit_mask.sum()) / max(int(ok.sum()), 1)
        else:
            agree = float('nan')

        # E 中哪一列承接了这些值
        best = []
        for s in val_cols:
            if s == c:
                continue
            close = sub[s].to_numpy() - dv
            hit = int((np.abs(close) <= tol).sum())
            if hit:
                best.append((hit, s))
        best.sort(reverse=True)
        top_hit = best[0] if best else (0, '-')
        cov = top_hit[0] / max(len(dv), 1)

        # E 侧该列整体填充率
        efill = int(B[c].notna().sum()) / max(n, 1)
        print('  %-28s 损失 %7d (%.1f%%行)  E填充率 %5.1f%%  同名一致率 %5.1f%%'
              % (c, k, 100.0 * k / n, 100.0 * efill, 100.0 * agree))
        print('      承接列: %-24s 命中 %d (%.1f%%)   候选: %s'
              % (top_hit[1], top_hit[0], 100.0 * cov,
                 ', '.join('%s=%d' % (nm, ht) for ht, nm in best[:3]) or '无'))
        rows.append((name, c, k, efill, agree, top_hit[1], top_hit[0], cov))

out = pd.DataFrame(rows, columns=[
    'table', 'col', 'loss_cells', 'e_fill_rate', 'same_name_agree',
    'carrier_col', 'carrier_hit', 'carrier_coverage'])
out.to_csv(r'D:\QuantLab\scripts\_audit_f13_rep.csv',
           index=False, encoding='utf-8-sig')
print()
print('报告: D:\\QuantLab\\scripts\\_audit_f13_rep.csv')
