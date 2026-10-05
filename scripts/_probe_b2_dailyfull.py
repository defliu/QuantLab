# coding=utf-8
"""核实 E:\temp\行情数据\个股行情全量\stock_daily_full.parquet 的身份（只读）。

关键问题：它是不是 D:\astock\daily\stock_daily.parquet 的全量版？
若行数/覆盖一致 -> 已在第一批迁过，无需再动；
若明显更大更新 -> 第一批可能迁的是增量，需重估。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

FULL = r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'
INCR = r'E:\temp\行情数据\stock_daily.parquet'
MAIN = r'D:\astock\daily\stock_daily.parquet'


def peek(path, tag):
    if not os.path.exists(path):
        print('%-26s 不存在' % tag)
        return None
    f = pq.ParquetFile(path)
    md = f.metadata
    names = list(f.schema_arrow.names)
    cols = [c for c in ('ts_code', 'trade_date') if c in names]
    t = f.read(columns=cols).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    td = pd.to_datetime(t['trade_date'], format='mixed', errors='coerce')
    codes = t['ts_code'].nunique() if 'ts_code' in t.columns else -1
    print('%-26s 行数=%9d  列数=%3d  股票数=%5d  %s ~ %s  %.2f MB'
          % (tag, md.num_rows, md.num_columns, codes,
             td.min().date(), td.max().date(),
             os.path.getsize(path) / 1024.0 / 1024.0))
    return {'rows': md.num_rows, 'cols': md.num_columns, 'codes': codes,
            'min': td.min(), 'max': td.max(), 'names': names,
            'mb': os.path.getsize(path) / 1024.0 / 1024.0}


print('=' * 100)
print('三方对照：网盘全量 vs E 增量 vs D 生产主库')
print('=' * 100)
a = peek(FULL, '网盘个股行情全量(full)')
b = peek(INCR, 'E stock_daily(增量)')
c = peek(MAIN, 'D astock 生产主库')

print()
print('=' * 100)
print('判定')
print('=' * 100)
if a and c:
    print('full 行数 vs 主库行数: %d vs %d  -> %s'
          % (a['rows'], c['rows'],
             '一致' if a['rows'] == c['rows'] else '不一致，主库可能是增量合并结果'))
    print('full 列数 vs 主库列数: %d vs %d  -> %s'
          % (a['cols'], c['cols'], '一致' if a['cols'] == c['cols'] else '不一致'))
    print('full 覆盖 ~ %s   主库覆盖 ~ %s' % (a['max'].date(), c['max'].date()))
    print()
    if a['rows'] == c['rows'] and a['cols'] == c['cols']:
        print('=> full 与生产主库结构/规模一致，第一批很可能已把它迁到位（全量换代已完成）。')
    else:
        print('=> 规模不一致，需进一步核对：full 是否含主库没有的股票或日期。')
    only_a = set(a['names']) - set(c['names'])
    only_c = set(c['names']) - set(a['names'])
    print()
    print('full 独有列: %s' % (sorted(only_a) or '无'))
    print('主库独有列: %s' % (sorted(only_c) or '无'))

if a and b:
    print()
    print('E 增量(%s~%s, %d 行) 是否被 full 完全覆盖: %s'
          % (b['min'].date(), b['max'].date(), b['rows'],
             '是' if (b['min'] >= a['min'] and b['max'] <= a['max']) else '否/越界'))