# -*- coding: utf-8 -*-
"""载荷差异性质定性（只读）。

PART D2 已确认：
    cashflow/balancesheet  E 与 D 载荷高度一致
    income/fina_indicator  E 与 D 载荷差异大

v1 缺陷（2026-10-03 修正）：
    1) 只对 E 做 drop_duplicates(keep='first')，D 未去重 -> merge 行数放大，
       C3/C6 的分子分母基数不一致，出现 >100% 的荒谬百分比。
    2) keep='first' 未落实 update_flag/f_ann_date 裁决，取到的是物理行序
       第一条，不是最新公告版本。

v2 对策：
    [C0] 双方各自去重前后行数、重复键数（暴露 D 侧自身也是多版本）
    两侧统一走同一确定性裁决：update_flag 大者优先 -> f_ann_date 晚者优先
    -> mergesort 稳定排序保原始行序，然后 drop_duplicates(keep='first')
    merge 前后断言严格 1:1。

对每表逐列统计：
    [C1] 双方均非空且数值差异的行占比        -> 真实值差
    [C2] E 有值 D 为 NaN                     -> E 补空
    [C3] D 有值 E 为 NaN                     -> E 丢值（全量替换会丢）
    [C4] 差异率>50% 列的 ratio=E/D 分布     -> 单位缩放判定
    [C5] 差异最大的 TopN 列
    [C6] D 有值/E 空 最多的 TopN 列（E 丢值风险）

只读：不写任何生产数据。
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
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]

DATE_COLS = ('end_date', 'ann_date', 'f_ann_date', 'change_date',
             'list_date', 'delist_date', 'trade_date', 'report_date')


def canon_date(s):
    """任意表示的日期 -> YYYYMMDD 字符串，缺失统一 'NA'。"""
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.strftime('%Y%m%d').fillna('NA').astype(str)

    t = s.astype(str).str.strip()
    t = t.where(~t.isin(('nan', 'NaT', 'None', '', '--')), 'NA')

    dg = t.str.split(' ').str[0].str.replace(r'\D', '', regex=True)
    out = dg.where(dg.str.len() == 8, 'NA')

    need = (out == 'NA') & (t != 'NA')
    if need.any():
        p = pd.to_datetime(t.where(need), errors='coerce')
        out = out.where(~need, p.dt.strftime('%Y%m%d').fillna('NA'))

    return out.fillna('NA').astype(str)


def canon_text(s):
    t = s.astype(str).str.strip()
    return t.where(~t.isin(('nan', 'NaT', 'None', '', '--')), 'NA')


def attach_key(df, keys):
    """把索引层主键搬回普通列，生成 '_k' 复合键。"""
    miss = [c for c in keys if c not in df.columns]
    if miss:
        have = [c for c in keys if c in (df.index.names or [])]
        if have:
            df = df.reset_index()

    parts = []
    for c in keys:
        if c not in df.columns:
            raise KeyError('%s 缺主键列 %s' % (list(df.columns)[:5], c))
        parts.append(canon_date(df[c]) if c in DATE_COLS
                     else canon_text(df[c]))

    out = df.copy()
    out['_k'] = parts[0].str.cat(others=parts[1:], sep='|')
    return out


def pick_version(df):
    """确定性版本裁决：update_flag 大者优先 -> f_ann_date 晚者优先。
    mergesort 稳定，裁决值相同时保留物理行序第一条。"""
    n0 = len(df)
    nk = df['_k'].nunique()

    if 'update_flag' in df.columns:
        uf = pd.to_numeric(df['update_flag'], errors='coerce').fillna(-1)
    else:
        uf = pd.Series(0.0, index=df.index)

    if 'f_ann_date' in df.columns:
        fa = pd.to_numeric(canon_date(df['f_ann_date']),
                           errors='coerce').fillna(0)
    else:
        fa = pd.Series(0.0, index=df.index)

    out = df.assign(_uf=uf, _fa=fa)
    out = out.sort_values(['_uf', '_fa'], ascending=[False, False],
                          kind='mergesort')
    out = out.drop_duplicates(subset=['_k'], keep='first')
    return out, n0, nk


def probe_table(tbl, keys, topn=10):
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')

    e = pd.read_parquet(ep)
    d = pd.read_parquet(dp)
    for x in (e, d):
        if 'index' in x.columns:
            x.drop(columns=['index'], inplace=True)

    e = attach_key(e, keys)
    d = attach_key(d, keys)

    ki = pd.Index(d['_k'].unique()).intersection(
        pd.Index(e['_k'].unique()))

    ed, en0, enk = pick_version(e[e['_k'].isin(ki)])
    dd, dn0, dnk = pick_version(d[d['_k'].isin(ki)])

    print()
    print('=' * 92)
    print('### %s   共同唯一键=%d' % (tbl, len(ki)))
    print('  [C0] D: 共同键子集原始行=%d 唯一键=%d 多余版本行=%d | '
          '裁决后行=%d' % (dn0, dnk, dn0 - dnk, len(dd)))
    print('       E: 共同键子集原始行=%d 唯一键=%d 多余版本行=%d | '
          '裁决后行=%d' % (en0, enk, en0 - enk, len(ed)))

    assert len(dd) == len(ed) == len(ki), \
        '裁决后行数与共同键数不等，1:1 不成立'
    assert not dd['_k'].duplicated().any(), 'D 裁决后仍有重复键'
    assert not ed['_k'].duplicated().any(), 'E 裁决后仍有重复键'

    shared = [c for c in d.columns if c in e.columns and c != '_k']
    num_cols = [c for c in shared
                if c not in keys
                and c not in ('update_flag', 'f_ann_date', 'comp_type')
                and (pd.api.types.is_numeric_dtype(e[c])
                     or pd.api.types.is_numeric_dtype(d[c]))]

    m = ed.merge(dd, on='_k', how='inner', suffixes=('_e', '_d'))
    assert len(m) == len(ki), 'merge 行数 != 共同键数'
    print('       数值载荷列=%d  merge 行=%d (严格 1:1)' % (len(num_cols), len(m)))

    tot_real = tot_both = tot_eonly = tot_donly = 0
    rows = []
    for c in num_cols:
        ve = pd.to_numeric(m[c + '_e'], errors='coerce')
        vd = pd.to_numeric(m[c + '_d'], errors='coerce')
        ne = ve.notna()
        nd = vd.notna()
        both = ne & nd
        nb = int(both.sum())
        if nb == 0:
            continue

        diff = ~np.isclose(ve[both], vd[both], rtol=1e-6, atol=1e-6,
                           equal_nan=True)
        ndiff = int(diff.sum())
        nz = vd[both] != 0
        ratio = (ve[both][nz] / vd[both][nz])
        ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()

        rows.append({
            'col': c,
            'both': nb,
            'ndiff': ndiff,
            'diff_rate': ndiff / nb,
            'e_only_val': int((ne & ~nd).sum()),
            'd_only_val': int((nd & ~ne).sum()),
            'r_med': float(ratio.median()) if len(ratio) > 50 else float('nan'),
        })
        tot_real += ndiff
        tot_both += nb
        tot_eonly += int((ne & ~nd).sum())
        tot_donly += int((nd & ~ne).sum())

    res = pd.DataFrame(rows)
    if res.empty:
        print('  [无可比数值列]')
        del e, d, m
        return

    print()
    print('  [C1] 双方均非空样本中数值不一致数 = %d / %d = %.3f%%'
          % (tot_real, tot_both,
             100.0 * tot_real / tot_both if tot_both else 0.0))
    print('  [C2] E有值/D为NaN = %d   （E 补空）' % tot_eonly)
    print('  [C3] D有值/E为NaN = %d   （E 丢值，全量替换会丢）' % tot_donly)

    nz = res[res['diff_rate'] > 0.5]
    print('  [C4] 差异率>50%% 的列数 = %d / %d' % (len(nz), len(res)))
    if len(nz):
        q = nz['r_med'].dropna()
        if len(q):
            print('       ratio=E/D 中位数: min=%.4g q25=%.4g med=%.4g '
                  'q75=%.4g max=%.4g'
                  % (q.min(), q.quantile(0.25), q.median(),
                     q.quantile(0.75), q.max()))

    print()
    print('  [C5] 差异最大的 Top%d 列:' % topn)
    for _, r in res.sort_values('ndiff', ascending=False).head(topn).iterrows():
        print('    %-28s 双方非空=%9d 差异=%8d(%5.1f%%) E补空=%7d E丢值=%8d '
              'ratio_med=%s'
              % (r['col'], r['both'], r['ndiff'], 100.0 * r['diff_rate'],
                 r['e_only_val'], r['d_only_val'],
                 ('%.4g' % r['r_med']) if pd.notna(r['r_med']) else 'NA'))

    dl = res[res['d_only_val'] > 0].sort_values('d_only_val',
                                                ascending=False).head(topn)
    print()
    print('  [C6] D有值/E为NaN 最多的 Top%d 列（E 丢值风险）:' % topn)
    print('       分母 = D 侧该列非空行数 = both + d_only_val')
    if dl.empty:
        print('    （无）')
    for _, r in dl.iterrows():
        dnn = r['both'] + r['d_only_val']
        print('    %-28s D有值=%8d 其中E空=%8d 丢失率=%6.2f%% 双方均非空=%d'
              % (r['col'], dnn, r['d_only_val'],
                 100.0 * r['d_only_val'] / max(dnn, 1), r['both']))

    del e, d, m, res


def main():
    which = sys.argv[1:] or [t for t, _ in FIN]
    for tbl, keys in FIN:
        if tbl in which:
            probe_table(tbl, keys)
    print()
    print('PAYLOAD_DIFF_DONE')


if __name__ == '__main__':
    main()
