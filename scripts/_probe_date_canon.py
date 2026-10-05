# -*- coding: utf-8 -*-
"""日期语义归一化后重算财务表真实键空间（只读）。

背景：_probe_donly_truth.py 证明 D 侧 end_date/ann_date 形如
'2023-12-31 00:00:00'（datetime），E 侧形如 '20260331'（YYYYMMDD 字符串）。
字符串直接拼接必然对不上，导致 100% 的历史键被判为 D-only（幽灵键）。

本脚本把所有日期列 canonicalize 为 YYYYMMDD 字符串后重算：
    common / D-only / E-only
从而得到"真实 D-only"——即 E 确实缺失、替换后会不可逆删除的记录。

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

FIN = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'share_change_event': ['ts_code', 'change_date', 'change_reason'],
}

DATE_COLS = {'end_date', 'ann_date', 'change_date', 'f_ann_date'}


def canon_date(s):
    """任意日期表示 -> YYYYMMDD 字符串；无法解析 -> 'NA'。"""
    def one(v):
        if pd.isna(v):
            return 'NA'
        if isinstance(v, (pd.Timestamp,)):
            return v.strftime('%Y%m%d')
        if hasattr(v, 'strftime'):
            return v.strftime('%Y%m%d')
        t = str(v).strip()
        if not t or t in ('nan', 'NaT', 'None', '--'):
            return 'NA'
        digits = ''.join(ch for ch in t.split(' ')[0] if ch.isdigit())
        if len(digits) == 8:
            return digits
        parsed = pd.to_datetime(t, errors='coerce')
        if pd.isna(parsed):
            return 'NA'
        return parsed.strftime('%Y%m%d')
    return s.map(one)


def norm(df, keys):
    out = df[keys].copy()
    for c in keys:
        if c in DATE_COLS:
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


def main():
    print('%-20s %10s %10s %10s %10s %10s %8s'
          % ('table', 'D_rows', 'E_rows', 'D_keys', 'E_keys',
             'D-only', 'E-only'))
    print('-' * 92)
    summary = []
    for tbl, keys in FIN.items():
        ep = os.path.join(E_ROOT, tbl + '.parquet')
        dp = os.path.join(D_BASE, 'finance', tbl + '.parquet')
        e = pd.read_parquet(ep, columns=keys)
        d = pd.read_parquet(dp, columns=keys)

        e['_k'] = rk(norm(e, keys), keys)
        d['_k'] = rk(norm(d, keys), keys)

        es = set(e['_k'])
        ds = set(d['_k'])
        d_only = ds - es
        e_only = es - ds
        common = ds & es

        print('%-20s %10d %10d %10d %10d %10d %8d'
              % (tbl, len(d), len(e), len(ds), len(es),
                 len(d_only), len(e_only)))

        donly_rows = int(d['_k'].isin(d_only).sum())
        eonly_rows = int(e['_k'].isin(e_only).sum())
        dup_e = int(len(e) - len(es))
        dup_d = int(len(d) - len(ds))
        print('    真实D-only行=%d  真实E-only行=%d  E内部多版本冗余行=%d  D内部冗余行=%d'
              % (donly_rows, eonly_rows, dup_e, dup_d))

        if d_only:
            sub = d[d['_k'].isin(d_only)].copy()
            for c in keys:
                if c in DATE_COLS:
                    sub[c] = canon_date(sub[c])
                else:
                    sub[c] = sub[c].map(
                        lambda v: 'NA' if pd.isna(v) else str(v).strip())
            print('    D-only 股票数=%d  end_date样例=%s'
                  % (sub['ts_code'].nunique(),
                     sorted(set(sub[keys[1]]))[:6]))
            if 'ann_date' in sub.columns:
                yr = sub['ann_date'].astype(str).str[:4]
                print('    D-only ann_date 年份分布: %s'
                      % dict(sorted(yr.value_counts().items())))
        summary.append((tbl, len(d_only), eonly_rows))
        del e, d

    print()
    print('=' * 92)
    print('真实 D-only 汇总（E 确实缺失、替换会删除）:')
    for t, k, r in summary:
        print('  %-20s D-only键=%6d  D-only行=%6d' % (t, k, r))
    print('DATE_CANON_DONE')


if __name__ == '__main__':
    main()