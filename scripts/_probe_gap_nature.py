# -*- coding: utf-8 -*-
"""缺口性质与重复行性质定性（只读，迁移门禁最后一批证据）。

PART A  日线：D-only 27562 是否 E 的日期缺口；E-only 50052 的日期分布
PART B  日线：D 在缺口 5 日与 E 全量末尾的覆盖对比
PART C  财务：D 侧多版本组到底靠什么区分（整行是否完全相同）
PART D  财务：归一化键空间下的载荷裁决（真实命中率）

只读：不写任何生产数据。
"""
import os
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'


def canon_date(s):
    def one(v):
        if pd.isna(v):
            return 'NA'
        if hasattr(v, 'strftime'):
            return v.strftime('%Y%m%d')
        t = str(v).strip()
        if not t or t in ('nan', 'NaT', 'None', '--'):
            return 'NA'
        dg = ''.join(ch for ch in t.split(' ')[0] if ch.isdigit())
        if len(dg) == 8:
            return dg
        p = pd.to_datetime(t, errors='coerce')
        return 'NA' if pd.isna(p) else p.strftime('%Y%m%d')
    return s.map(one)


def norm(df, keys):
    out = df[keys].copy()
    for c in keys:
        if c.endswith('_date'):
            out[c] = canon_date(out[c])
        else:
            out[c] = out[c].map(
                lambda v: 'NA' if pd.isna(v) else str(v).strip())
    return out


def rk(df, keys):
    r = df[keys[0]]
    for c in keys[1:]:
        r = r + '|' + df[c]
    return r


def part_a_daily_gap():
    print('=' * 92)
    print('### PART A/B  日线缺口性质')
    dp = os.path.join(D_BASE, 'daily', 'stock_daily.parquet')
    ep = os.path.join(E_ROOT, '个股行情全量', 'stock_daily_full.parquet')

    d = pd.read_parquet(dp, columns=['trade_date', 'ts_code']).reset_index()
    e = pd.read_parquet(ep, columns=['trade_date', 'ts_code'])
    if 'index' in e.columns:
        e = e.drop(columns=['index'])
    d['_k'] = rk(norm(d, ['trade_date', 'ts_code']), ['trade_date', 'ts_code'])
    e['_k'] = rk(norm(e, ['trade_date', 'ts_code']), ['trade_date', 'ts_code'])
    d['_dt'] = canon_date(d['trade_date'])
    e['_dt'] = canon_date(e['trade_date'])

    es = set(e['_k'])
    ds = set(d['_k'])
    donly = d[~d['_k'].isin(es)]
    eonly = e[~e['_k'].isin(ds)]

    print('  [A1] D-only 行的日期分布: %s'
          % dict(sorted(donly['_dt'].value_counts().items())))
    print('  [A2] E 全量是否含这些日期: %s'
          % {d_: int((e['_dt'] == d_).sum())
             for d_ in sorted(donly['_dt'].unique())})
    print('  [A3] D 在这些日期的行数: %s'
          % {d_: int((d['_dt'] == d_).sum())
             for d_ in sorted(donly['_dt'].unique())})

    print('  [A4] E-only 行的日期分布(前12): %s'
          % dict(sorted(eonly['_dt'].value_counts().items(),
                        reverse=True)[:12]))
    print('  [A5] E-only 日期区间: %s ~ %s'
          % (eonly['_dt'].min(), eonly['_dt'].max()))
    print('  [A6] D 末日=%s  E 末日=%s  E 起始=%s  D 起始=%s'
          % (d['_dt'].max(), e['_dt'].max(), e['_dt'].min(), d['_dt'].min()))

    # 缺口 5 日在 D/E 的股票覆盖
    for d_ in sorted(donly['_dt'].unique()):
        dc = set(d.loc[d['_dt'] == d_, 'ts_code'].astype(str))
        ec = set(e.loc[e['_dt'] == d_, 'ts_code'].astype(str))
        print('  [A7] %s  D股票数=%d  E股票数=%d  E独有=%d  D独有=%d'
              % (d_, len(dc), len(ec), len(ec - dc), len(dc - ec)))

    # D 尾部区间每日股票数 vs E 尾部
    print('  [A8] D 末 12 个交易日: %s'
          % dict(sorted(d['_dt'].value_counts().items())[-12:]))
    print('  [A9] E 末 12 个交易日: %s'
          % dict(sorted(e['_dt'].value_counts().items())[-12:]))
    del d, e


def part_c_dup_nature():
    print()
    print('=' * 92)
    print('### PART C  财务 D 侧多版本组的区分字段（整行是否完全相同）')
    cases = [
        ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
        ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ]
    for tbl, keys in cases:
        dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
        d = pd.read_parquet(dp)
        if 'index' in d.columns:
            d = d.drop(columns=['index'])
        d['_k'] = rk(norm(d, keys), keys)
        dup = d['_k'].value_counts()
        multi = set(dup[dup > 1].index)
        print()
        print('  --- %s  多版本键=%d ---' % (tbl, len(multi)))
        sub = d[d['_k'].isin(multi)].copy()
        # 区分字段：update_flag / f_ann_date / comp_type
        for c in ['update_flag', 'f_ann_date', 'comp_type', 'report_type']:
            if c in sub.columns:
                g = sub.groupby('_k')[c].nunique(dropna=False)
                print('     区分字段 %-14s 组内唯一值数分布: %s'
                      % (c, dict(g.value_counts().sort_index())))
        # 整行哈希是否相同
        val_cols = [c for c in d.columns
                    if c not in keys and c != '_k']
        hv = pd.util.hash_pandas_object(
            sub[val_cols].astype(str), index=False)
        hv = pd.Series(hv.values, index=sub.index)
        g = hv.groupby(sub['_k']).nunique()
        same = int((g == 1).sum())
        print('     载荷列数=%d  多版本组中载荷完全相同=%d / %d (%.1f%%)'
              % (len(val_cols), same, len(g), 100.0 * same / max(len(g), 1)))
        del d, sub


def part_d_payload_adjudicate():
    print()
    print('=' * 92)
    print('### PART D  归一化键空间下的载荷裁决（真实命中率）')
    cases = [
        ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
        ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
        ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ]
    for tbl, keys in cases:
        ep = os.path.join(E_ROOT, tbl + '.parquet')
        dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
        e = pd.read_parquet(ep)
        d = pd.read_parquet(dp)
        for x in (e, d):
            if 'index' in x.columns:
                x.drop(columns=['index'], inplace=True)
        e['_k'] = rk(norm(e, keys), keys)
        d['_k'] = rk(norm(d, keys), keys)

        shared = [c for c in d.columns if c in e.columns and c != '_k']
        num_cols = []
        for c in shared:
            if c in keys or c in ('update_flag', 'f_ann_date', 'comp_type'):
                continue
            if pd.api.types.is_numeric_dtype(e[c]) or \
                    pd.api.types.is_numeric_dtype(d[c]):
                num_cols.append(c)
        print()
        print('  --- %s 共有列=%d 数值载荷列=%d ---' % (tbl, len(shared), len(num_cols)))

        # 只在“共同键”上比对；E 多版本 → 允许任一版本命中
        ds = set(d['_k'])
        esub = e[e['_k'].isin(ds)]
        dsub = d[d['_k'].isin(ds)]
        m = esub.merge(dsub, on='_k', how='inner',
                       suffixes=('_e', '_d'))
        print('    merge 行数=%d  （E多版本导致行放大）' % len(m))
        all_eq = np.ones(len(m), dtype=bool)
        for c in num_cols:
            all_eq &= np.isclose(
                pd.to_numeric(m[c + '_e'], errors='coerce'),
                pd.to_numeric(m[c + '_d'], errors='coerce'),
                rtol=1e-6, atol=1e-6, equal_nan=True)
        m['_ok'] = all_eq
        per = m.groupby('_k')['_ok'].any()
        print('    共同键=%d  全载荷命中键=%d (%.2f%%)'
              % (len(per), int(per.sum()),
                 100.0 * per.mean() if len(per) else 0.0))

        # 用“至少 80% 数值列相同”放宽，看版本选取是否只是个别列差
        agree_cnt = pd.Series(0, index=m['_k'].values)
        for c in num_cols:
            ok = np.isclose(
                pd.to_numeric(m[c + '_e'], errors='coerce'),
                pd.to_numeric(m[c + '_d'], errors='coerce'),
                rtol=1e-6, atol=1e-6, equal_nan=True)
            agree_cnt = agree_cnt.add(pd.Series(ok, index=m['_k'].values),
                                      fill_value=0)
        thr = int(len(num_cols) * 0.8)
        near = agree_cnt[agree_cnt >= thr]
        print('    >=80%%数值列一致的键=%d (%.2f%%)  阈值=%d列'
              % (len(near), 100.0 * len(near) / max(len(per), 1), thr))
        del e, d, m


if __name__ == '__main__':
    part_a_daily_gap()
    part_c_dup_nature()
    part_d_payload_adjudicate()
    print()
    print('GAP_NATURE_DONE')