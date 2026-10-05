# -*- coding: utf-8 -*-
"""方法论证伪探针（只读）：验证"与E各版本都不等"是否为浮点比较过严导致的假象。

三件事：
1. 日线列集合差异（E 33 列 vs D 31 列）+ 关键列 dtype 对比；
2. fina_indicator 抽样多版本键，打印 D 与 E 各版本的载荷列原值（repr 全精度），
   人工判定是 ULP 级差异还是实质差异；
3. 同一批键在 rtol=0/atol=1e-9 与 rtol=1e-6 两种口径下的命中率对比。
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
E_FULL_DIR = os.path.join(E_ROOT, '个股行情全量')
D_BASE = r'D:\astock'

FIN_TABLES = {
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
}


def section(t):
    print()
    print('=' * 92)
    print(t)


def probe_daily_columns():
    section('### A. 日线列集合与 dtype 差异')
    e = pd.read_parquet(os.path.join(E_FULL_DIR, 'stock_daily_full.parquet'))
    d = pd.read_parquet(os.path.join(D_BASE, 'daily', 'stock_daily.parquet'))
    ebody = e.reset_index()
    dbody = d.reset_index()

    ec, dc = set(ebody.columns), set(dbody.columns)
    print('  E 列数=%d  D 列数=%d' % (len(ec), len(dc)))
    print('  E 独有列: %s' % sorted(ec - dc))
    print('  D 独有列: %s' % sorted(dc - ec))
    print('  共有列数=%d' % len(ec & dc))

    common = sorted(ec & dc)
    print('  共有列 dtype 差异:')
    n_diff = 0
    for c in common:
        a, b = ebody[c], dbody[c]
        if str(a.dtype) != str(b.dtype):
            n_diff += 1
            if n_diff <= 20:
                print('      %-18s E=%-16s D=%s' % (c, str(a.dtype), str(b.dtype)))
    print('      共 %d 列 dtype 不同' % n_diff)


def _norm_keys(df, keys):
    """把键列统一成字符串，避免 NaN/日期/字符串 dtype 不一致导致 merge 失败。"""
    out = df[keys].copy()
    for c in keys:
        out[c] = out[c].map(lambda v: 'NA' if pd.isna(v) else str(v).strip())
    return out


def _rk(df, keys):
    """逐列拼接成稳定键字符串，避免 dtype 差异导致的 join 失败。"""
    cols = [df[c].map(lambda v: 'NA' if pd.isna(v) else str(v).strip())
            for c in keys]
    rk = cols[0]
    for c in cols[1:]:
        rk = rk + '|' + c
    return rk


def probe_version_match(tbl, keys, sample=6):
    section('### B. %s 版本裁决（真实值抽样）' % tbl)
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')

    e = pd.read_parquet(ep)
    d = pd.read_parquet(dp)
    if 'index' in e.columns:
        e = e.drop(columns=['index'])
    e['_flag'] = e['update_flag'].map(
        lambda v: -1 if pd.isna(v) else int(round(float(str(v).strip() or -1)))
    )

    e['_rk2'] = _rk(_norm_keys(e, keys), keys)
    d['_rk'] = _rk(_norm_keys(d, keys), keys)

    dup_rk = e['_rk2'].value_counts()
    multi_rk = set(dup_rk[dup_rk > 1].index)
    shared = sorted(set(d['_rk']) & multi_rk)
    print('  多版本键=%d  与 D 交集=%d' % (len(multi_rk), len(shared)))
    if not shared:
        return

    # 选载荷列：组内有差异的列优先
    payload = [c for c in e.columns
               if c not in keys and c not in ('update_flag', '_flag', '_rk2', '_rk', 'index')
               and c in d.columns]
    g = e[e['_rk2'].isin(shared)].groupby('_rk2')
    varying = [c for c in payload if int((g[c].nunique(dropna=False) > 1).sum()) > 0]
    show = (varying[:4] or payload[:4])
    print('  载荷列(优先取组内有差异者): %s' % show)

    hit_strict, hit_relax = 0, 0
    rng = np.random.default_rng(20261001)
    pick = list(rng.choice(shared, size=min(sample, len(shared)), replace=False))
    for i, rk in enumerate(pick):
        ev = e[e['_rk2'] == rk]
        dv = d[d['_rk'] == rk]
        if dv.empty:
            continue
        dv = dv.iloc[0]
        print('  --- 样本%d key=%s' % (i + 1, rk))
        for c in show:
            evs = ev[c].tolist()
            dvv = dv[c]
            def s(x):
                if pd.isna(x):
                    return 'NaN'
                if isinstance(x, (float, np.floating)):
                    return repr(float(x))
                return str(x)[:40]
            print('        %-12s D=%-24s E=%s' % (c, s(dvv), [s(x) for x in evs]))

        strict_ok = False
        relax_ok = False
        for _, er in ev.iterrows():
            for c in show:
                a, b = er[c], dv[c]
                if pd.isna(a) and pd.isna(b):
                    continue
                try:
                    if not np.isclose(pd.to_numeric(a, errors='coerce'),
                                      pd.to_numeric(b, errors='coerce'),
                                      rtol=0, atol=1e-9):
                        strict_ok = False
                        break
                    else:
                        relax_ok = relax_ok or np.isclose(
                            pd.to_numeric(a, errors='coerce'),
                            pd.to_numeric(b, errors='coerce'),
                            rtol=1e-6, atol=1e-6)
                except Exception:
                    if str(a) != str(b):
                        strict_ok = False
                        relax_ok = False
                        break
            else:
                strict_ok = True
                continue
            break
        hit_strict += int(strict_ok)
        hit_relax += int(relax_ok)

    print('  >> 抽样 %d 组：严格口径(rtol=0,atol=1e-9) 命中 %d；'
          '放宽口径(rtol=1e-6,atol=1e-6) 命中 %d'
          % (len(pick), hit_strict, hit_relax))


def probe_hitrate(tbl, keys):
    section('### C. %s 版本命中率 + 键覆盖 + 选取规则判定' % tbl)
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
    e = pd.read_parquet(ep)
    d = pd.read_parquet(dp)
    if 'index' in e.columns:
        e = e.drop(columns=['index'])
    e['_flag'] = e['update_flag'].map(
        lambda v: -1 if pd.isna(v) else int(round(float(str(v).strip() or -1)))
    )

    d['_rk'] = _rk(_norm_keys(d, keys), keys)
    e['_rk2'] = _rk(_norm_keys(e, keys), keys)

    d_rk = set(d['_rk'])
    e_rk = set(e['_rk2'])
    print('  D 键=%d  E 键=%d  交集=%d  D-only=%d  E-only=%d'
          % (len(d_rk), len(e_rk), len(d_rk & e_rk),
             len(d_rk - e_rk), len(e_rk - d_rk)))

    dup_rk = e['_rk2'].value_counts()
    multi_rk = set(dup_rk[dup_rk > 1].index) & d_rk
    print('  多版本键∩D = %d' % len(multi_rk))
    if not multi_rk:
        return

    payload = [c for c in e.columns
               if c not in keys and c not in ('update_flag', '_flag', '_rk2', '_rk', 'index')
               and c in d.columns]
    # 只比对数值型载荷列，规避 dtype/文本归一化噪声
    num_cols = [c for c in payload
                if pd.api.types.is_numeric_dtype(e[c])
                and pd.api.types.is_numeric_dtype(d[c])]
    print('  数值载荷列=%d/%d' % (len(num_cols), len(payload)))
    if not num_cols:
        return

    em = e[e['_rk2'].isin(multi_rk)][['_rk2', '_flag'] + num_cols]
    dm = d[d['_rk'].isin(multi_rk)][['_rk'] + num_cols]
    m = em.merge(dm, left_on='_rk2', right_on='_rk', how='inner',
                 suffixes=('_e', '_d'))
    print('  merge 行数=%d' % len(m))
    if m.empty:
        print('  [WARN] merge 无匹配')
        return

    def eq_vec(a, b, rtol, atol):
        return np.isclose(pd.to_numeric(a, errors='coerce'),
                          pd.to_numeric(b, errors='coerce'),
                          rtol=rtol, atol=atol, equal_nan=True)

    for rtol, atol, tag in ((0, 1e-9, '严格 rtol=0 atol=1e-9'),
                            (1e-6, 1e-6, '放宽 rtol=1e-6 atol=1e-6')):
        all_eq = np.ones(len(m), dtype=bool)
        for c in num_cols:
            all_eq &= eq_vec(m[c + '_e'], m[c + '_d'], rtol, atol)
        per_key = m.assign(_ok=all_eq).groupby('_rk2')['_ok'].any()
        vc = per_key.value_counts().to_dict()
        print('    %-26s 至少一版命中=%7d/%d (%.1f%%)'
              % (tag, int(vc.get(True, 0)), len(per_key),
                 100.0 * vc.get(True, 0) / len(per_key)))

    # 严格口径下，D 命中的是哪个 flag 版本
    all_eq = np.ones(len(m), dtype=bool)
    for c in num_cols:
        all_eq &= eq_vec(m[c + '_e'], m[c + '_d'], 0, 1e-9)
    mm = m.assign(_ok=all_eq)
    matched = mm[mm['_ok']]
    if len(matched):
        vc = matched['_flag'].value_counts().to_dict()
        tot = len(matched)
        print('    D 精确命中的 E 版本分布: %s' % vc)
        for f in (0, 1, -1):
            if f in vc:
                print('        flag=%-3s %8d (%5.1f%%)'
                      % (f, vc[f], 100.0 * vc[f] / tot))


def main():
    probe_daily_columns()
    for tbl, keys in FIN_TABLES.items():
        probe_version_match(tbl, keys)
        probe_hitrate(tbl, keys)
    print()
    print('METHOD_DONE')


if __name__ == '__main__':
    main()