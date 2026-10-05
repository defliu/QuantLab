# -*- coding: utf-8 -*-
"""D-only 键真伪与列分歧方向定性（只读）。

回答三个决定性问题：
1. D-only 是"幽灵键"（日期格式差异）还是 E 真的缺该报告期？
   → 检查该 ts_code 在 E 中是否存在、E 的 end_date 有哪些格式；
2. 分歧列是 E 补上了 D 的空值（E 更好），还是 E 把 D 的值弄丢了（E 更差）？
   → 统计 NaN 方向；
3. 抽样 D-only 行与对应 ts_code 的 E 侧记录，供人工核对。
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


def _norm_keys(df, keys):
    out = df[keys].copy()
    for c in keys:
        out[c] = out[c].map(lambda v: 'NA' if pd.isna(v) else str(v).strip())
    return out


def _rk(df, keys):
    cols = [df[c].map(lambda v: 'NA' if pd.isna(v) else str(v).strip())
            for c in keys]
    rk = cols[0]
    for c in cols[1:]:
        rk = rk + '|' + c
    return rk


def probe_donly(tbl, keys):
    print()
    print('=' * 92)
    print('### %s  D-only 真伪鉴定' % tbl)
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_BASE, 'finance', tbl + '.parquet'))
    if 'index' in e.columns:
        e = e.drop(columns=['index'])

    e['_rk2'] = _rk(_norm_keys(e, keys), keys)
    d['_rk'] = _rk(_norm_keys(d, keys), keys)
    donly = d[~d['_rk'].isin(set(e['_rk2']))].copy()
    print('  D-only 行数=%d  股票数=%d' % (len(donly), donly['ts_code'].nunique()))

    # 1) 该 ts_code 在 E 中是否完全不存在
    e_codes = set(e['ts_code'].astype(str))
    codes = donly['ts_code'].astype(str)
    in_e = codes.isin(e_codes)
    print('  [1] D-only 的股票在 E 中存在=%d / 不存在=%d'
          % (int(in_e.sum()), int((~in_e).sum())))

    # 2) end_date 格式是否一致（幽灵键主因）
    print('  [2] end_date 格式检查')
    print('      D 样例: %s' % sorted(donly['end_date'].astype(str).unique())[:5])
    sub_codes = codes[in_e].unique()[:400]
    esub = e[e['ts_code'].astype(str).isin(set(sub_codes))]
    print('      E 样例: %s' % sorted(esub['end_date'].astype(str).unique())[:5])
    de = set(donly['end_date'].astype(str))
    ee = set(esub['end_date'].astype(str))
    print('      仅 D 有的 end_date 格式数=%d；仅 E 有的=%d；共有=%d'
          % (len(de - ee), len(ee - de), len(de & ee)))
    if de - ee:
        print('      仅D有样例: %s' % sorted(de - ee)[:5])

    # 3) 同一 (code, end_date) 在 E 中到底有没有
    k2 = [k for k in keys if k != 'ann_date']
    e['_k2'] = _rk(_norm_keys(e, k2), k2)
    donly['_k2'] = _rk(_norm_keys(donly, k2), k2)
    e_k2 = set(e.loc[e['ts_code'].astype(str).isin(set(codes[in_e])), '_k2'])
    hit = donly['_k2'].isin(e_k2)
    print('  [3] D-only 的 (code,end_date) 在 E 同股票范围内存在=%d (%.1f%%)'
          % (int(hit.sum()), 100.0 * hit.mean()))

    # 4) 抽样人工核对
    print('  [4] 抽样 5 行 D-only 及其股票在 E 的最新 3 条记录')
    for _, r in donly.head(5).iterrows():
        code = str(r['ts_code'])
        er = e[e['ts_code'].astype(str) == code]
        er = er.sort_values('ann_date').tail(3)
        print('      D-only: %s' % ' | '.join(
            '%s=%s' % (k, r[k]) for k in keys))
        for _, er2 in er.iterrows():
            print('        E 同期: %s' % ' | '.join(
                '%s=%s' % (k, er2[k]) for k in keys))


def probe_nan_direction(tbl, keys, cols):
    print()
    print('=' * 92)
    print('### %s  分歧列的 NaN 方向（E 补空 vs E 丢值）' % tbl)
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_BASE, 'finance', tbl + '.parquet'))
    if 'index' in e.columns:
        e = e.drop(columns=['index'])

    # 只用单版本键，避免多版本交叉放大噪声
    e['_rk2'] = _rk(_norm_keys(e, keys), keys)
    d['_rk'] = _rk(_norm_keys(d, keys), keys)
    dup = e['_rk2'].value_counts()
    single = set(dup[dup == 1].index) & set(d['_rk'])
    print('  单版本共同键=%d' % len(single))
    if not single:
        return

    em = e[e['_rk2'].isin(single)][['_rk2'] + cols]
    dm = d[d['_rk'].isin(single)][['_rk'] + cols]
    m = em.merge(dm, left_on='_rk2', right_on='_rk', how='inner',
                 suffixes=('_e', '_d'))
    print('  merge 行数=%d' % len(m))
    for c in cols:
        if c not in m.columns:
            continue
        a = pd.to_numeric(m[c + '_e'], errors='coerce')
        b = pd.to_numeric(m[c + '_d'], errors='coerce')
        e_na, d_na = a.isna(), b.isna()
        e_fill = int((e_na & ~d_na).sum())
        d_fill = int((~e_na & d_na).sum())
        both = int((e_na & d_na).sum())
        diff = int((~np.isclose(a, b, rtol=1e-6, atol=1e-6,
                               equal_nan=True)).sum())
        print('    %-22s E补空=%7d  E丢值=%7d  双空=%7d  数值不同=%7d'
              % (c, e_fill, d_fill, both, diff))


def main():
    probe_donly('fina_indicator', ['ts_code', 'end_date', 'ann_date'])
    probe_donly('income', ['ts_code', 'end_date', 'report_type', 'ann_date'])
    probe_nan_direction('fina_indicator',
                        ['ts_code', 'end_date', 'ann_date'],
                        ['non_op_profit', 'ocf_to_or', 'roic_yearly',
                         'inv_turn', 'eps', 'dt_eps'])
    probe_nan_direction('income',
                        ['ts_code', 'end_date', 'report_type', 'ann_date'],
                        ['total_opcost', 'oth_income', 'continued_net_profit',
                         'credit_impa_loss', 'ebit', 'ebitda'])
    print()
    print('DONLY_TRUTH_DONE')


if __name__ == '__main__':
    main()