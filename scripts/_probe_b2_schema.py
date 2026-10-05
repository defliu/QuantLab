# coding=utf-8
"""查清 E 盘几「读取失败」表的真实 schema（只读）。

ths_daily / top_list / moneyflow_ind_ths 报 'trade_date' 缺列，
且 report_rc_daily / share_change_event / stock_basic_data 未识别出日期列。
需要确认：日期/键字段究竟是数据列、还是被存成了 pandas 索引（MultiIndex）。
这直接决定前面覆盖审计的键集合是否可信。
"""
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock'

TARGETS = [
    ('ths_daily', E_ROOT + r'\ths_daily.parquet', D_ROOT + r'\board\ths_daily.parquet'),
    ('top_list', E_ROOT + r'\top_list.parquet', D_ROOT + r'\lhb\top_list.parquet'),
    ('moneyflow_ind_ths', E_ROOT + r'\moneyflow_ind_ths.parquet',
     D_ROOT + r'\board_fundflow\moneyflow_ind_ths.parquet'),
    ('report_rc_daily', E_ROOT + r'\report_rc_daily.parquet',
     D_ROOT + r'\research\report_rc_daily.parquet'),
    ('share_change_event', E_ROOT + r'\share_change_event.parquet',
     D_ROOT + r'\finance\share_change_event.parquet'),
    ('stock_basic', E_ROOT + r'\stock_basic_data.parquet',
     D_ROOT + r'\basic\stock_basic.parquet'),
]

for tag, ep, dp in TARGETS:
    print('=' * 92)
    print('%s' % tag)
    print('=' * 92)
    for side, p in (('E 供应商', ep), ('D 生产库', dp)):
        if not p:
            continue
        import os
        if not os.path.exists(p):
            print('  [%s] 文件不存在: %s' % (side, p))
            continue
        f = pq.ParquetFile(p)
        md = f.metadata
        names = list(f.schema_arrow.names)
        print('  [%s] 行数=%d 列数=%d' % (side, md.num_rows, md.num_columns))
        print('  [%s] arrow 列名: %s' % (side, names))
        # pandas 元数据里的索引信息
        try:
            t = f.read(columns=None)
            tp = t.to_pandas()
            if isinstance(tp.index, pd.MultiIndex):
                print('  [%s] pandas 索引=MultiIndex names=%s  -> 这些键是索引不是数据列'
                      % (side, list(tp.index.names)))
                print('  [%s] 数据列: %s' % (side, list(tp.columns)[:14]))
            else:
                print('  [%s] pandas 索引=%s name=%s'
                      % (side, type(tp.index).__name__, tp.index.name))
                print('  [%s] 数据列: %s' % (side, list(tp.columns)[:14]))
        except Exception as exc:
            print('  [%s] 全表读取失败: %r' % (side, exc))
        print()
