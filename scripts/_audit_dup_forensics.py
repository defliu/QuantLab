# -*- coding: utf-8 -*-
"""定位 E 盘财务表主键重复行的真实区分维度，并对照 D 盘主库口径。只读。"""
import os
import sys

import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'


def probe(tbl, keys, cand_cols):
    e = pd.read_parquet(os.path.join(E_ROOT, tbl + '.parquet'))
    d = pd.read_parquet(os.path.join(D_FIN, tbl + '.parquet'))
    print('=' * 90)
    print('### %s' % tbl)
    print('  E: %d 行, 唯一主键 %d, D: %d 行, 唯一主键 %d'
          % (len(e), len(e) - int(e.duplicated(subset=keys).sum()),
             len(d), len(d) - int(d.duplicated(subset=keys).sum())))

    for c in cand_cols:
        if c not in e.columns:
            print('    [列不存在] %s' % c)
            continue
        # 该列能否完全解释重复：主键 + 该列 是否唯一
        key2 = keys + [c]
        uniq2 = len(e) - int(e.duplicated(subset=key2).sum())
        print('    主键+%-16s 唯一行=%7d  (E 行数 %d, 残留重复 %d)'
              % (c, uniq2, len(e), len(e) - uniq2))

    # 每组重复的行数分布
    g = e.groupby(keys, dropna=False).size()
    print('    重复组规模分布: 1行=%d, 2行=%d, 3+行=%d'
          % (int((g == 1).sum()), int((g == 2).sum()), int((g > 2).sum())))


probe('fina_indicator', ['ts_code', 'end_date', 'ann_date'],
      ['f_ann_date', 'comp_type', 'end_type', 'report_type', 'update_flag', 'q_ann_date'])
print()
probe('income', ['ts_code', 'end_date', 'report_type', 'ann_date'],
      ['f_ann_date', 'comp_type', 'end_type', 'update_flag', 'q_ann_date'])
print()
probe('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date'],
      ['f_ann_date', 'comp_type', 'end_type', 'update_flag'])
print()
probe('forecast', ['ts_code', 'end_date', 'ann_date', 'type'],
      ['first_ann_date', 'update_flag'])
print()
probe('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc'],
      ['record_date', 'ex_date', 'pay_date', 'imp_ann_date', 'div_listdate'])
