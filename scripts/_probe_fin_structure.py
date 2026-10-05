# -*- coding: utf-8 -*-
"""真实键空间基础上的结构性对位审计（只读）。

回答三个决定性问题：
1. D 侧的内部多版本是否同样由 update_flag 区分？
   → 若是，则 E 的多版本形态与 D 同构，直接覆盖不破坏"多版本"契约；
2. 归一化后残余的真实 D-only（fina_indicator 2 行 / forecast 417 行）
   具体是什么，E 是否真的没有该报告期；
3. 日线 D-only 27562 是否也是日期格式假象（用统一日期归一化复核）。

只读：不写任何生产数据。
"""
import os
import sys

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
        digits = ''.join(ch for ch in t.split(' ')[0] if ch.isdigit())
        if len(digits) == 8:
            return digits
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


def part1_multi_version_structure():
    print('=' * 92)
    print('### PART 1  D/E 多版本结构对位（update_flag 是否同构）')
    cases = [
        ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
        ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
        ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ]
    for tbl, keys in cases:
        ep = os.path.join(E_ROOT, tbl + '.parquet')
        dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
        e = pd.read_parquet(ep, columns=keys + ['update_flag'])
        d = pd.read_parquet(dp, columns=keys + ['update_flag'])
        for x, nm in ((e, 'E'), (d, 'D')):
            x['_k'] = rk(norm(x, keys), keys)
        print()
        print('  --- %s ---' % tbl)
        for x, nm in ((d, 'D'), (e, 'E')):
            dup = x['_k'].value_counts()
            multi = dup[dup > 1]
            print('    %s 行数=%d  键=%d  多版本键=%d  冗余行=%d'
                  % (nm, len(x), len(dup), len(multi), len(x) - len(dup)))
            # 冗余组内 update_flag 是否互不相同？
            if len(multi):
                sub = x[x['_k'].isin(multi.index)]
                g = sub.groupby('_k')['update_flag'].nunique(dropna=False)
                print('       冗余组内 update_flag 唯一值数分布: %s'
                      % dict(g.value_counts().sort_index()))
                print('       update_flag 取值: %s'
                      % dict(sub['update_flag'].value_counts(dropna=False)))
        del e, d


def _read_cols(path, want):
    have = set(pq.ParquetFile(path).schema_arrow.names)
    return [c for c in want if c in have]


def part2_real_donly():
    print()
    print('=' * 92)
    print('### PART 2  归一化后真实 D-only 明细')
    cases = [
        ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
        ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ]
    for tbl, keys in cases:
        ep = os.path.join(E_ROOT, tbl + '.parquet')
        dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
        extra = _read_cols(ep, ['update_flag', 'f_ann_date'])
        print('  [%s] 实际读取附加列=%s' % (tbl, extra))
        e = pd.read_parquet(ep, columns=keys + extra)
        d = pd.read_parquet(dp, columns=keys + extra)
        e['_k'] = rk(norm(e, keys), keys)
        d['_k'] = rk(norm(d, keys), keys)
        donly = d[~d['_k'].isin(set(e['_k']))]
        print()
        print('  --- %s  真实D-only 行数=%d ---' % (tbl, len(donly)))
        if not len(donly):
            continue
        print(donly.head(8).to_string())
        # E 里是否有同 report_period 但 ann_date 不同
        k2 = [k for k in keys if k != 'ann_date']
        e['_k2'] = rk(norm(e, k2), k2)
        donly = donly.copy()
        donly['_k2'] = rk(norm(donly, k2), k2)
        codes = set(donly['ts_code'].astype(str))
        esub = e[e['ts_code'].astype(str).isin(codes)]
        print('    这些股票在 E 中的记录数=%d，end_date 集合=%s'
              % (len(esub), sorted(set(esub['end_date'].astype(str)))[:12]))
        print('    D-only 的 end_date 集合=%s'
              % sorted(set(donly['end_date'].astype(str)))[:12])
        e_k2 = set(esub['_k2'])
        hit = donly['_k2'].isin(e_k2)
        print('    D-only 的 (code,end_date,type) 在 E 同股票内存在=%d/%d'
              % (int(hit.sum()), len(donly)))
        if hit.any():
            ex = donly[hit].head(3)
            for _, r in ex.iterrows():
                print('      D-only 行: %s' % ' | '.join(
                    '%s=%s' % (k, r[k]) for k in keys))
                cand = esub[esub['_k2'] == r['_k2']]
                for _, c2 in cand.iterrows():
                    print('        E 同报告期: %s | update_flag=%s'
                          % (' | '.join('%s=%s' % (k, c2[k]) for k in keys),
                             c2.get('update_flag', 'NA')))
        del e, d


def part3_daily_keyspace():
    print()
    print('=' * 92)
    print('### PART 3  日线键空间用统一日期归一化复核（只读 trade_date+ts_code）')
    dp = os.path.join(D_BASE, 'daily', 'stock_daily.parquet')
    ep = os.path.join(E_ROOT, '个股行情全量', 'stock_daily_full.parquet')

    # D 的 trade_date 是 index level0，pandas 会重建为 index；统一 reset_index
    d = pd.read_parquet(dp, columns=['trade_date', 'ts_code'])
    d = d.reset_index()
    d['_k'] = rk(norm(d, ['trade_date', 'ts_code']), ['trade_date', 'ts_code'])
    print('  D 行数=%d  归一化后键数=%d  冗余=%d'
          % (len(d), d['_k'].nunique(), len(d) - d['_k'].nunique()))

    e = pd.read_parquet(ep, columns=['trade_date', 'ts_code'])
    if 'index' in e.columns:
        e = e.drop(columns=['index'])
    e['_k'] = rk(norm(e, ['trade_date', 'ts_code']), ['trade_date', 'ts_code'])
    print('  E 行数=%d  归一化后键数=%d  冗余=%d'
          % (len(e), e['_k'].nunique(), len(e) - e['_k'].nunique()))

    es = set(e['_k'])
    ds = set(d['_k'])
    print('  共同键=%d' % len(ds & es))
    print('  D-only 键=%d  D-only 行=%d'
          % (len(ds - es), int(d['_k'].isin(ds - es).sum())))
    print('  E-only 键=%d  E-only 行=%d'
          % (len(es - ds), int(e['_k'].isin(es - ds).sum())))
    if ds - es:
        dk = d[d['_k'].isin(ds - es)]
        print('  D-only end_date 分布(前15): %s'
              % dict(sorted(dk['trade_date'].astype(str).value_counts().items(),
                            reverse=True)[:15]))


if __name__ == '__main__':
    part2_real_donly()
    part3_daily_keyspace()
    print()
    print('STRUCT_AUDIT_DONE')