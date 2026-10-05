# -*- coding: utf-8 -*-
"""查 D 主库对 update_flag 的既有取舍策略，以及 E 同主键双行的版本差异特征。只读。"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

KEYS = {
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
}

for tbl, keys in KEYS.items():
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_FIN, tbl + '.parquet'))
    e = e.reset_index(drop=True)
    d = d.reset_index(drop=True)
    print('=' * 88)
    print('### %s' % tbl)

    # 只看 E 中同主键有 2 个 flag 的组
    g = e.groupby(keys, dropna=False)['update_flag'].nunique(dropna=False)
    multi = g[g > 1].index
    e2 = e.set_index(keys).loc[multi].reset_index()
    d2 = d.set_index(keys)

    # D 库对同一主键保留了哪个 flag
    dk = d.set_index(keys).index
    cnt = d2.groupby(keys, dropna=False).size().value_counts().to_dict()
    dsub = d2[d2.index.isin(dk)]
    if len(dsub):
        print('  D 库覆盖到的 E 双flag主键数=%d，D 侧 flag 分布=%s'
              % (len(dsub), dsub['update_flag'].value_counts(dropna=False).to_dict()))
        # 同一主键 D 有几行
        print('  这些主键在 D 库的行数分布=%s（1=唯一）' % cnt)

    # E 双 flag 组的 f_ann_date 关系（若存在该列）
    if 'f_ann_date' in e.columns:
        sub = e2[['ts_code'] + keys[1:] + ['update_flag', 'f_ann_date']].head(6)
        print('  E 双flag组样例（flag / f_ann_date）:')
        print(sub.to_string(index=False))
    print('  E 双flag组中 flag=1 行数=%d, flag=0 行数=%d'
          % (int((e2['update_flag'] == 1).sum()), int((e2['update_flag'] == 0).sum())))
