# -*- coding: utf-8 -*-
"""验证假设：D 的 q_* 单季度字段为空，E 已填充（快照版本差异，非版本选择问题）。只读。"""
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

CASES = (
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date'],
     ['q_eps', 'q_opincome', 'q_profit_yoy', 'q_netprofit_yoy',
      'non_op_profit', 'op_to_debt', 'profit_prefin_exp', 'fcff', 'fcfe']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date'],
     ['free_cashflow', 'others', 'incl_cash_rec_saims']),
)


def audit(tbl, keys, cols):
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_BASE, 'finance', tbl + '.parquet'))
    e = e.drop(columns=[c for c in ('index',) if c in e.columns])

    print('=' * 92)
    print('### %s  (E行=%d D行=%d)' % (tbl, len(e), len(d)))

    print('  各列非空率对比:')
    print('      %-24s %10s %10s' % ('列', 'D非空率', 'E非空率'))
    for c in cols:
        if c not in d.columns or c not in e.columns:
            print('      %-24s  [列缺失 D=%s E=%s]' % (c, c in d.columns, c in e.columns))
            continue
        dn = float(d[c].notna().mean())
        en = float(e[c].notna().mean())
        print('      %-24s %9.2f%% %9.2f%%' % (c, dn * 100, en * 100))

    # 只在「两库共有键」上比，排除键集合差异干扰
    dk = pd.MultiIndex.from_frame(d[keys])
    ek = pd.MultiIndex.from_frame(e[keys])
    common = dk.intersection(ek)
    print('  共有主键数=%d' % len(common))

    sample = pd.MultiIndex.from_tuples(list(common)[:3000],
                                      names=keys).to_frame(index=False)
    ds = d.merge(sample, on=keys, how='inner')
    es = e.merge(sample, on=keys, how='inner')

    print('  在共有键样本(D=%d行,E=%d行)上：D空 / E非空 的计数:'
          % (len(ds), len(es)))
    for c in cols:
        if c not in ds.columns or c not in es.columns:
            continue
        # 对齐到同一 (key, 版本) 粒度：按键聚合「是否存在非空」
        a = ds.groupby(keys, dropna=False)[c].apply(lambda s: s.notna().any())
        b = es.groupby(keys, dropna=False)[c].apply(lambda s: s.notna().any())
        j = pd.concat([a, b], axis=1, keys=['d', 'e']).fillna(False)
        both_na = int((~j['d'] & ~j['e']).sum())
        d_only = int((j['d'] & ~j['e']).sum())
        e_only = int((~j['d'] & j['e']).sum())
        both = int((j['d'] & j['e']).sum())
        print('      %-24s 两空=%5d  仅D有=%5d  仅E有=%6d  都有=%5d'
              % (c, both_na, d_only, e_only, both))


if __name__ == '__main__':
    for tbl, keys, cols in CASES:
        audit(tbl, keys, cols)
