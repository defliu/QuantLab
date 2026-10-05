# -*- coding: utf-8 -*-
"""全表扫描：E 全量替换会摧毁哪些列（只读）。

判定规则（对每张财务表、每个 D/E 共有的数值列）：
    d_cov = D 共同键裁决版该列非空率
    e_cov = E 共同键裁决版该列非空率
  DESTRUCTED : d_cov >= 0.30 且 e_cov < 0.05   -> E 近乎全空，替换即摧毁
  LOST       : d_cov >= 0.30 且 e_cov < 0.50*d_cov -> E 显著变稀
  OK         : 其余

裁决版：每键按 (update_flag desc, f_ann_date desc) 取一条，
        避免稀疏的 uf=0 行被优先选中造成伪差异。

同时输出每张表的 D-only 键（E 不可补回的行）。

只读：不写任何生产数据。
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

DATE_KEYS = {'end_date', 'ann_date', 'f_ann_date', 'change_date'}

TABLES = [
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]

DESTROY_D = 0.30
DESTROY_E = 0.05


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


def build_key(df, keys):
    parts = []
    for c in keys:
        s = df[c]
        if c in DATE_KEYS:
            s = canon_date(s)
        else:
            s = s.astype(str).str.strip()
            s = s.where(~s.isin(['nan', 'NaT', 'None', '', '--']), 'NA')
        parts.append(s.astype(str))
    return parts[0].str.cat(others=parts[1:], sep='|')


def verdict(df):
    d = df
    d['_uf'] = (pd.to_numeric(d['update_flag'], errors='coerce')
                .fillna(-1)) if 'update_flag' in d.columns else 0
    d['_fa'] = (pd.to_numeric(canon_date(d['f_ann_date']), errors='coerce')
                .fillna(0)) if 'f_ann_date' in d.columns else 0
    d = d.sort_values(['_uf', '_fa'], ascending=[False, False],
                      kind='mergesort')
    return d.drop_duplicates(subset=['_k'], keep='first')


def scan(tbl, keys):
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
    e = pd.read_parquet(ep)
    d = pd.read_parquet(dp)
    for x in (e, d):
        if 'index' in x.columns:
            x.drop(columns=['index'], inplace=True)

    e['_k'] = build_key(e, keys)
    d['_k'] = build_key(d, keys)
    ds = set(d['_k'])
    es = set(e['_k'])

    donly_k = ds - es
    eonly_k = es - ds
    common = ds & es

    dv = verdict(d[d['_k'].isin(common)])
    ev = verdict(e[e['_k'].isin(common)])

    shared = [c for c in d.columns if c in e.columns and c != '_k']

    rows = []
    for c in shared:
        dser = pd.to_numeric(dv[c], errors='coerce')
        eser = pd.to_numeric(ev[c], errors='coerce')
        dn = int(dser.notna().sum())
        en = int(eser.notna().sum())
        dcov = dn / len(dv) if len(dv) else 0.0
        ecov = en / len(ev) if len(ev) else 0.0
        if dcov >= DESTROY_D and ecov < DESTROY_E:
            tag = 'DESTRUCTED'
        elif dcov >= DESTROY_D and ecov < 0.5 * dcov:
            tag = 'LOST'
        else:
            tag = 'OK'
        rows.append((c, tag, dn, len(dv), dcov, en, len(ev), ecov))

    rows.sort(key=lambda r: (r[1] != 'DESTRUCTED', r[1] != 'LOST', r[4]))

    print()
    print('=' * 104)
    print('### %s' % tbl)
    print('  D行=%d  E行=%d  共同键=%d  D-only键=%d(行%d)  E-only键=%d(行%d)'
          % (len(d), len(e), len(common), len(donly_k),
             int(d['_k'].isin(donly_k).sum()), len(eonly_k),
             int(e['_k'].isin(eonly_k).sum())))
    print('  共有列=%d  其中 DESTRUCTED=%d  LOST=%d'
          % (len(shared),
             sum(1 for r in rows if r[1] == 'DESTRUCTED'),
             sum(1 for r in rows if r[1] == 'LOST')))
    print('  %-24s %-11s %-16s %-16s %s'
          % ('column', 'verdict', 'D非空', 'E非空', 'covD -> covE'))

    bad = [r for r in rows if r[1] != 'OK']
    if not bad:
        print('  （无摧毁/显著变稀列）')
    for c, tag, dn, dt, dcov, en, et, ecov in bad:
        print('  %-24s %-11s %6d/%-8d %6d/%-8d %.3f -> %.3f'
              % (c, tag, dn, dt, en, et, dcov, ecov))

    return [r[0] for r in rows if r[1] == 'DESTRUCTED'], \
           [r[0] for r in rows if r[1] == 'LOST']


def main():
    all_destroy = {}
    all_lost = {}
    for tbl, keys in TABLES:
        de, lo = scan(tbl, keys)
        all_destroy[tbl] = de
        all_lost[tbl] = lo

    print()
    print('=' * 104)
    print('### 汇总：DESTRUCTED（E 近乎全空，替换即摧毁）')
    for tbl, cols in all_destroy.items():
        if cols:
            print('  %-22s %d 列: %s' % (tbl, len(cols), ', '.join(cols)))
    print()
    print('### 汇总：LOST（E 显著变稀）')
    for tbl, cols in all_lost.items():
        if cols:
            print('  %-22s %d 列: %s' % (tbl, len(cols), ', '.join(cols)))
    print()
    print('COLUMN_LOSS_DONE')


if __name__ == '__main__':
    main()