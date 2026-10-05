# -*- coding: utf-8 -*-
"""E 盘财务表主键重复行核查：判断重复是「同内容冗余」还是「主键定义不全导致有效数据被丢弃」。

只读，不写任何文件。
"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'

FIN_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
}

for tbl, keys in FIN_KEYS.items():
    p = os.path.join(E_ROOT, tbl + '.parquet')
    df = pd.read_parquet(p)
    dupmask = df.duplicated(subset=keys, keep=False)
    n_dup = int(dupmask.sum())
    print('=' * 78)
    print('%-18s 总行=%d  主键重复行=%d  唯一主键=%d'
          % (tbl, len(df), n_dup, int(len(df) - df.duplicated(subset=keys).sum())))
    if n_dup == 0:
        continue
    sub = df.loc[dupmask]
    # 同一主键组内，是否存在取值不同的列
    diff_cols = []
    for c in df.columns:
        if c in keys:
            continue
        try:
            nun = sub.groupby(keys, dropna=False)[c].nunique(dropna=False)
        except TypeError:
            continue
        if int((nun > 1).sum()) > 0:
            diff_cols.append((c, int((nun > 1).sum())))
    if diff_cols:
        print('  重复组内取值不同的列（前 12）:')
        for c, n in diff_cols[:12]:
            print('    %-28s 有差异的主键组=%d' % (c, n))
        print('  → 判定: 主键定义不全，keep=last 会丢有效数据，需修主键')
    else:
        print('  重复组内所有非主键列取值一致 → 判定: 纯冗余重复，去重安全')
    # 样例
    k0 = keys[0]
    first_key = sub[keys].iloc[0].tolist()
    m = pd.Series(True, index=sub.index)
    for k, v in zip(keys, first_key):
        if pd.isna(v):
            m &= sub[k].isna()
        else:
            m &= sub[k] == v
    print('  样例主键 %s 的重复行数=%d' % (first_key, int(m.sum())))
    print(sub.loc[m].head(6).to_string(max_colwidth=18))
    print()
