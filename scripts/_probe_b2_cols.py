# coding=utf-8
"""对比 FULL / MAIN 的列名与 schema 差异（只读）。"""
import sys

import pyarrow.parquet as pq

PAIRS = [
    ('FULL', r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'),
    ('INCR', r'E:\temp\行情数据\stock_daily.parquet'),
    ('MAIN', r'D:\astock\daily\stock_daily.parquet'),
]


def main():
    info = {}
    for tag, p in PAIRS:
        f = pq.ParquetFile(p)
        names = list(f.schema_arrow.names)
        info[tag] = names
        print('%-5s 列数=%d' % (tag, len(names)))
        print('      %s' % names)
        print('')

    if 'FULL' in info and 'MAIN' in info:
        fs, ms = set(info['FULL']), set(info['MAIN'])
        print('MAIN 独有列: %s' % sorted(ms - fs))
        print('FULL 独有列: %s' % sorted(fs - ms))

    return 0


if __name__ == '__main__':
    sys.exit(main())