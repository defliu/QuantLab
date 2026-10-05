# -*- coding: utf-8 -*-
"""修正版载荷裁决 + 缺口补救来源排查（只读）。

修正 1：>=80% 统计按 _k 去重（此前 agree_cnt 索引含重复导致 >100%）
修正 2：查 E 盘所有 parquet 是否存在能补 20260629~20260703 的日线文件
"""
import os
import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

FIN = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
]


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


def part_d2():
    print('=' * 92)
    print('### PART D2  载荷裁决（去重修正版）')
    for tbl, keys in FIN:
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
        num_cols = [c for c in shared
                    if c not in keys
                    and c not in ('update_flag', 'f_ann_date', 'comp_type')
                    and (pd.api.types.is_numeric_dtype(e[c])
                         or pd.api.types.is_numeric_dtype(d[c]))]
        ds = set(d['_k'])
        m = e[e['_k'].isin(ds)].merge(d[d['_k'].isin(ds)], on='_k',
                                      how='inner', suffixes=('_e', '_d'))
        n_rows = len(m)
        agree = np.zeros(n_rows, dtype=np.int32)
        allok = np.ones(n_rows, dtype=bool)
        for c in num_cols:
            ok = np.isclose(pd.to_numeric(m[c + '_e'], errors='coerce'),
                            pd.to_numeric(m[c + '_d'], errors='coerce'),
                            rtol=1e-6, atol=1e-6, equal_nan=True)
            agree += ok.astype(np.int32)
            allok &= ok
        m['_agree'] = agree
        m['_all'] = allok
        per_max = m.groupby('_k').agg(_mx=('_agree', 'max'),
                                      _ok=('_all', 'any'))
        nk = len(per_max)
        print()
        print('  --- %s 共有列=%d 数值载荷列=%d 共同键=%d merge行=%d ---'
              % (tbl, len(shared), len(num_cols), nk, n_rows))
        print('    全载荷命中键=%d (%.2f%%)'
              % (int(per_max['_ok'].sum()),
                 100.0 * per_max['_ok'].mean() if nk else 0.0))
        for thr in (0.5, 0.8, 0.95):
            cnt = int((per_max['_mx'] >= thr * len(num_cols)).sum())
            print('    >=%.0f%%数值列一致(阈值%d列)的键=%d (%.2f%%)'
                  % (thr * 100, int(thr * len(num_cols)), cnt,
                     100.0 * cnt / nk if nk else 0.0))
        del e, d, m


def part_e_gap_source():
    print()
    print('=' * 92)
    print('### PART E  缺口补救来源：E 盘哪些文件含 20260629~20260703 日线')
    target = {'20260629', '20260630', '20260701', '20260702', '20260703'}
    for root, dirs, files in os.walk(E_ROOT):
        for fn in sorted(files):
            if not fn.lower().endswith('.parquet'):
                continue
            p = os.path.join(root, fn)
            try:
                import pyarrow.parquet as pq
                cols = set(pq.ParquetFile(p).schema_arrow.names)
            except Exception as ex:
                print('  [skip] %s  %s' % (p, ex))
                continue
            if 'trade_date' not in cols:
                print('  [无 trade_date] %s' % os.path.relpath(p, E_ROOT))
                continue
            try:
                df = pd.read_parquet(p)
            except Exception as ex:
                print('  [读失败] %s  %s' % (p, ex))
                continue
            if 'trade_date' not in df.columns:
                print('  [索引层无该列] %s' % os.path.relpath(p, E_ROOT))
                continue
            s = set(canon_date(df['trade_date']).unique())
            hit = sorted(s & target)
            rng = (min(s), max(s)) if s else ('-', '-')
            print('  %-46s 行数=%8d 日期范围=%s~%s 命中缺口日=%s'
                  % (os.path.relpath(p, E_ROOT), len(df), rng[0], rng[1],
                     hit if hit else '无'))


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if which in ('all', 'd2'):
        part_d2()
    if which in ('all', 'e'):
        part_e_gap_source()
    print()
    print('GAP_FIX_DONE')