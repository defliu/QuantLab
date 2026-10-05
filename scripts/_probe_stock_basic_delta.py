# -*- coding: utf-8 -*-
"""stock_basic 增量明细 + 字段值级差异（只读）。

回答三个问题：
  1. E-only 的 9 个 ts_code 是什么（新股？）
  2. 共同 5904 个代码上，E 相对 D 有哪些字段值变化（替换是否会改数据）
  3. list_date=1970-01-01 的 epoch-0 哨兵有多少（影响 list_date 口径）
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

DP = r'D:\astock\basic\stock_basic.parquet'
EP = r'E:\temp\行情数据\stock_basic_data.parquet'


def say(*a):
    print(*a)
    sys.stdout.flush()


def main():
    d = pd.read_parquet(DP)
    e = pd.read_parquet(EP)
    say('D 列=%s' % list(d.columns))
    say('')

    d['_k'] = d['ts_code'].astype(str).str.strip()
    e['_k'] = e['ts_code'].astype(str).str.strip()

    eonly = sorted(set(e['_k']) - set(d['_k']))
    say('E-only ts_code (%d 个): %s' % (len(eonly), eonly))
    if eonly:
        cols = [c for c in ('ts_code', 'name', 'list_date', 'delist_date',
                             'market', 'exchange', 'list_status')
                if c in e.columns]
        say('  [E 侧明细]')
        for rec in e.loc[e['_k'].isin(eonly), cols].to_dict('records'):
            say('    %s' % (rec,))
    say('')

    common = pd.Index(set(d['_k']) & set(e['_k']))
    dv = d.loc[d['_k'].isin(common)].sort_values('_k').reset_index(drop=True)
    ev = e.loc[e['_k'].isin(common)].sort_values('_k').reset_index(drop=True)
    assert (dv['_k'].values == ev['_k'].values).all(), '共同键顺序未对齐'

    say('共同键=%d，逐列差异:' % len(dv))
    for c in dv.columns:
        if c == '_k':
            continue
        a, b = dv[c], ev[c]
        same_na = a.isna() & b.isna()
        eq = (a.astype('object').where(~same_na, '__NA__').values
              == b.astype('object').where(~same_na, '__NA__').values)
        ndiff = int((~eq).sum())
        if ndiff:
            say('  %-14s 差异=%d' % (c, ndiff))
            idx = (~eq).nonzero()[0][:3]
            for i in idx:
                say('      %s : D=%r  E=%r' % (dv['_k'].iloc[i],
                                               a.iloc[i], b.iloc[i]))
        else:
            say('  %-14s 完全一致' % c)
    say('')

    for tag, df in (('D', d), ('E', e)):
        ld = pd.to_datetime(df['list_date'], errors='coerce')
        ep0 = int((ld == pd.Timestamp('1970-01-01')).sum())
        say('[%s] list_date: min=%s max=%s  epoch0哨兵=%d  null=%d'
            % (tag, ld.min(), ld.max(), ep0, int(ld.isna().sum())))
        dd = pd.to_datetime(df['delist_date'], errors='coerce')
        say('[%s] delist_date: 非空=%d  epoch0哨兵=%d  null=%d'
            % (tag, int(dd.notna().sum()),
               int((dd == pd.Timestamp('1970-01-01')).sum()),
               int(dd.isna().sum())))
    say('')
    say('SB_DONE')


if __name__ == '__main__':
    main()
