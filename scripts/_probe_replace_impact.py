# -*- coding: utf-8 -*-
"""全量替换影响面审计（只读）：回答"整体替换会删掉/改掉什么"。

对每张财务表回答三个问题：
1. 逐列分歧：D 命中的 E 版本上，哪些列一致、哪些列实质不同（定位真实差异列）；
2. D-only 键的性质：是"被 E 淘汰的旧版本"还是"独有报表"（决定删除是否安全）；
3. E-only 键的性质：新增价值。
输出严格限量。
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

FIN_TABLES = {
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
}


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


def load(tbl):
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_BASE, 'finance', tbl + '.parquet'))
    if 'index' in e.columns:
        e = e.drop(columns=['index'])
    return e, d


def col_divergence(tbl, keys, e, d):
    """逐列分歧：找出到底哪些列在 D/E 之间不同。"""
    print('-' * 92)
    print('  [1] 逐列分歧定位')
    e['_rk2'] = _rk(_norm_keys(e, keys), keys)
    d['_rk'] = _rk(_norm_keys(d, keys), keys)

    shared = set(d['_rk'])
    dup = e['_rk2'].value_counts()
    multi = set(dup[dup > 1].index) & shared

    payload = [c for c in e.columns
               if c not in keys and c not in ('update_flag', '_rk2', '_rk', 'index')
               and c in d.columns]
    num_cols = [c for c in payload
                if pd.api.types.is_numeric_dtype(e[c])
                and pd.api.types.is_numeric_dtype(d[c])]

    # 每个键取「首个数值全等的 E 版本」作为 D 的对位版本
    em = e[e['_rk2'].isin(multi)][['_rk2'] + num_cols]
    dm = d[d['_rk'].isin(multi)][['_rk'] + num_cols]
    m = em.merge(dm, left_on='_rk2', right_on='_rk', how='inner',
                 suffixes=('_e', '_d'))
    if m.empty:
        print('      merge 无匹配')
        return

    diff_cnt = {}
    for c in num_cols:
        a = pd.to_numeric(m[c + '_e'], errors='coerce')
        b = pd.to_numeric(m[c + '_d'], errors='coerce')
        ok = np.isclose(a, b, rtol=1e-6, atol=1e-6, equal_nan=True)
        diff_cnt[c] = int((~ok).sum())

    n = len(m)
    same = [c for c, v in diff_cnt.items() if v == 0]
    diff = sorted([(c, v) for c, v in diff_cnt.items() if v > 0],
                  key=lambda x: -x[1])
    print('      对位行数=%d  完全一致列=%d/%d  有差异列=%d'
          % (n, len(same), len(num_cols), len(diff)))
    print('      差异最大 TOP12: %s' % diff[:12])

    # 只用「一致列」重算命中率 → 验证差异是否集中在少数列
    if same:
        all_eq = np.ones(n, dtype=bool)
        for c in same:
            a = pd.to_numeric(m[c + '_e'], errors='coerce')
            b = pd.to_numeric(m[c + '_d'], errors='coerce')
            all_eq &= np.isclose(a, b, rtol=1e-6, atol=1e-6, equal_nan=True)
        hit = m.assign(_ok=all_eq).groupby('_rk2')['_ok'].any().sum()
        print('      仅用一致列(%d列)重算: 至少一版命中=%d/%d (%.1f%%)'
              % (len(same), hit, len(multi), 100.0 * hit / len(multi)))


def donly_nature(tbl, keys, e, d):
    """D-only 键的性质：是被 E 淘汰的旧公告，还是独有报表。"""
    print('-' * 92)
    print('  [2] D-only 键性质（替换后将被删除）')
    e['_rk2'] = _rk(_norm_keys(e, keys), keys)
    d['_rk'] = _rk(_norm_keys(d, keys), keys)
    donly = d[~d['_rk'].isin(set(e['_rk2']))]

    print('      D-only 行数=%d  涉及股票=%d'
          % (len(donly), donly['ts_code'].nunique()))

    ad = 'ann_date' if 'ann_date' in donly.columns else None
    if ad:
        s = donly[ad].astype(str).str.replace('.0$', '', regex=True)
        print('      ann_date 范围: %s ~ %s' % (s.min(), s.max()))
        # 年份分布
        yr = s.str[:4]
        vc = yr.value_counts().sort_index()
        print('      ann_date 年份分布(尾部8个): %s' % vc.tail(8).to_dict())

    # 关键：同一 (ts_code, end_date) 在 E 里是否有其他公告版本？
    k2 = [k for k in keys if k != 'ann_date']
    if all(k in donly.columns for k in k2) and all(k in e.columns for k in k2):
        e2 = e.copy()
        e2['_k2'] = _rk(_norm_keys(e, k2), k2)
        d2 = donly.copy()
        d2['_k2'] = _rk(_norm_keys(d2, k2), k2)
        e_k2 = set(e2['_k2'])
        # D-only 的 (code,end_date) 在 E 中另有较晚公告 → 属"旧公告被淘汰"
        has_newer = d2['_k2'].isin(e_k2)
        print('      其中 (code,end_date) 在 E 另有公告版本 = %d (%.1f%%)'
              % (int(has_newer.sum()), 100.0 * has_newer.mean()))
        print('      → 判定: 旧公告版本被 E 淘汰 = %d；E 中完全无此报告期 = %d'
              % (int(has_newer.sum()), int((~has_newer).sum())))


def eonly_gain(tbl, keys, e, d):
    """E-only 键的性质：迁移的新增价值。"""
    print('-' * 92)
    print('  [3] E-only 键性质（迁移的新增）')
    e['_rk2'] = _rk(_norm_keys(e, keys), keys)
    d['_rk'] = _rk(_norm_keys(d, keys), keys)
    eonly = e[~e['_rk2'].isin(set(d['_rk']))]
    print('      E-only 行数=%d  涉及股票=%d'
          % (len(eonly), eonly['ts_code'].nunique()))
    if 'ann_date' in eonly.columns:
        s = eonly['ann_date'].astype(str).str.replace('.0$', '', regex=True)
        print('      ann_date 范围: %s ~ %s' % (s.min(), s.max()))
        print('      年份分布: %s' % s.str[:4].value_counts().sort_index().to_dict())
    if 'update_flag' in eonly.columns:
        print('      update_flag 分布: %s'
              % eonly['update_flag'].value_counts().to_dict())


def main():
    for tbl, keys in FIN_TABLES.items():
        print()
        print('=' * 92)
        print('### %s' % tbl)
        e, d = load(tbl)
        col_divergence(tbl, keys, e, d)
        donly_nature(tbl, keys, e, d)
        eonly_gain(tbl, keys, e, d)
    print()
    print('REPLACE_IMPACT_DONE')


if __name__ == '__main__':
    main()