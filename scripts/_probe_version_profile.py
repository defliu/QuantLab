# -*- coding: utf-8 -*-
"""E 侧多版本的字段覆盖画像 + 版本裁决对比（只读）。

背景：_probe_payload_diff.py 发现 fina_indicator / income 存在海量
"D 有值 / E 为空"。但该脚本对 E 每个键只取了第一条版本
（drop_duplicates keep='first'），而 E 侧 update_flag 有 0/1 双版本，
低版本字段可能更稀 -> 可能是取样偏差，也可能是真实覆盖缺失。

本脚本：
  1. 正确做键归一化（日期 canonicalize，非日期列 strip），修掉旧版
     "归一化后又被 rk(原始列) 覆盖" 的 bug；
  2. 按 update_flag 分组统计各列非空率；
  3. 额外给出「裁决版」——每键按 (update_flag desc, f_ann_date desc)
     取第一条——的非空率，用以区分"取样偏差"与"真实缺失"；
  4. 与 D 同口径对照。

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

TARGET = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date'],
     ['non_op_profit', 'ocf_to_or', 'profit_prefin_exp', 'fcff', 'fcfe',
      'fcff_ps', 'fcfe_ps', 'networking_capital', 'cash_ratio', 'assets_yoy',
      'eqt_yoy', 'bps_yoy', 'roic', 'grossprofit_margin', 'netprofit_margin',
      'debt_to_assets']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date'],
     ['ebit', 'ebitda', 'total_opcost', 'oth_income', 'continued_net_profit',
      'credit_impa_loss', 'asset_disp_income', 'total_cogs', 'n_income',
      'operate_profit', 'oper_profit', 'total_profit', 'n_income_attr_p',
      'oper_cost', 'compr_inc_attr_p', 't_compr_income', 'total_cogs']),
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


def build_key(df, keys):
    """按键拼 _k；日期列做语义归一化，非日期列仅 strip。"""
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


def pick_verdict(df, keys):
    """每键取一条：update_flag 优先为 1，其次 f_ann_date 最新。"""
    d = df.copy()
    d['_uf'] = (pd.to_numeric(d['update_flag'], errors='coerce')
                .fillna(-1)) if 'update_flag' in d.columns else 0
    d['_fa'] = (pd.to_numeric(canon_date(d['f_ann_date']), errors='coerce')
                .fillna(0)) if 'f_ann_date' in d.columns else 0
    d = d.sort_values(['_uf', '_fa'], ascending=[False, False], kind='mergesort')
    return d.drop_duplicates(subset=['_k'], keep='first')


def cov(g, c):
    sr = pd.to_numeric(g[c], errors='coerce')
    n = int(sr.notna().sum())
    return '%d/%d' % (n, len(g))


def probe(tbl, keys, cols):
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
    e = pd.read_parquet(ep)
    d = pd.read_parquet(dp)
    for x in (e, d):
        if 'index' in x.columns:
            x.drop(columns=['index'], inplace=True)

    print()
    print('=' * 104)
    print('### %s   E行=%d  D行=%d' % (tbl, len(e), len(d)))

    missing = [c for c in cols if c not in e.columns or c not in d.columns]
    for c in missing:
        print('  %-24s [E有=%s D有=%s] 跳过'
              % (c, c in e.columns, c in d.columns))
    cols = [c for c in cols if c not in missing]

    e['_k'] = build_key(e, keys)
    d['_k'] = build_key(d, keys)
    ds = set(d['_k'])
    ec = e[e['_k'].isin(ds)].copy()
    dc = d[d['_k'].isin(ds)].copy()

    flagcol = 'update_flag' if 'update_flag' in ec.columns else None
    print('  共同键=%d   共同键内 E行=%d  D行=%d'
          % (len(ds & set(ec['_k'])), len(ec), len(dc)))
    if flagcol:
        print('  共同键内 E update_flag 分布=%s'
              % dict(sorted(pd.to_numeric(ec[flagcol],
                                          errors='coerce')
                            .value_counts().items())))
        n1 = ec.loc[pd.to_numeric(ec[flagcol],
                                  errors='coerce') == 1, '_k'].nunique()
        print('  共同键中「有 update_flag=1 版本」的键数=%d / %d'
              % (n1, len(ds & set(ec['_k']))))
    print('  共同键 D冗余行=%d   E共同键冗余行=%d'
          % (len(dc) - dc['_k'].nunique(),
             len(ec) - ec['_k'].nunique()))

    groups = [('E:全部', ec)]
    if flagcol:
        for fv, g in ec.groupby(
                pd.to_numeric(ec[flagcol], errors='coerce')):
            groups.append(('E:uf=%g' % fv, g))
    groups.append(('E:裁决版', pick_verdict(ec, keys)))
    groups.append(('D:全部', dc))
    groups.append(('D:裁决版', pick_verdict(dc, keys)))

    hdr = '  %-22s' % 'column'
    for name, _ in groups:
        hdr += ' %15s' % name
    print()
    print(hdr)
    print('  ' + '-' * (24 + 16 * len(groups)))

    for c in cols:
        line = '  %-22s' % c
        for _, g in groups:
            line += ' %15s' % cov(g, c)
        print(line)

    del e, d, ec, dc


def main():
    for tbl, keys, cols in TARGET:
        probe(tbl, keys, cols)
    print()
    print('VERSION_PROFILE_DONE')


if __name__ == '__main__':
    main()