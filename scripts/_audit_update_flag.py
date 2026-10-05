# -*- coding: utf-8 -*-
"""确认 update_flag 语义 + D/E 主库口径一致性，只读不改。

关键问题：D 盘主库是否已按 update_flag 去重？E 盘重复是否等价于「同一财报的多个抓取版本」？
若 D 已过滤而 E 未过滤，直接把 E 合进 D 会造成主库同一财报出现 update_flag=0/1 两条，
下游 PIT 读取（按 ann_date 取最新）可能取到旧版本 → 财务因子口径漂移。
"""
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
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
}

for tbl, keys in KEYS.items():
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_FIN, tbl + '.parquet'))
    print('=' * 88)
    print('### %s   keys=%s' % (tbl, '+'.join(keys)))
    for tag, df in (('D主库', d), ('E源', e)):
        if 'update_flag' not in df.columns:
            print('   %s: 无 update_flag 列（行数 %d）' % (tag, len(df)))
            continue
        vc = df['update_flag'].value_counts(dropna=False).to_dict()
        # 同一主键下 update_flag 的取值个数分布
        g = df.groupby(keys, dropna=False)['update_flag'].nunique(dropna=False)
        print('   %s: 行数=%d  update_flag分布=%s  同主键flag取值数分布=%s'
              % (tag, len(df), vc, {int(k): int(v) for k, v in g.value_counts().items()}))
        if len(df) and int(df.duplicated(subset=keys).sum()) == 0:
            print('      -> 该库同主键唯一（已去重）')
