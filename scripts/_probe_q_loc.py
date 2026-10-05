# coding=utf-8
"""定位 run_screen.py 实际消费的 26 个财务衍生字段在哪些表、哪些 dtype。

初版 _diag_q_fields.py 报「q_ 目标命中 0/8」，需先确认 q_* 字段真实归属，
否则整章反解都在错目标上做。

只读，不写生产文件。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

D_FIN = r'D:\astock\finance'

FIN_FIELDS = [
    'bps', 'roe', 'gross_margin', 'netprofit_margin', 'ocfps', 'eps',
    'current_ratio', 'quick_ratio', 'debt_to_assets', 'profit_dedt',
    'netprofit_yoy', 'dt_netprofit_yoy', 'or_yoy', 'ocf_yoy', 'roe_yoy',
    'assets_yoy', 'tr_yoy', 'q_roe', 'q_gsprofit_margin',
    'q_netprofit_margin', 'q_sales_qoq', 'q_netprofit_qoq',
    'q_dtprofit_to_profit', 'q_ocf_to_sales', 'dtprofit_to_profit',
    'ocf_to_profit', 'ocf_to_or',
]

files = sorted(f for f in os.listdir(D_FIN) if f.endswith('.parquet'))
schema_map = {}
for f in files:
    p = os.path.join(D_FIN, f)
    sch = pq.ParquetFile(p).schema_arrow
    schema_map[f] = (set(sch.names), sch, pq.ParquetFile(p).metadata.num_rows)

print('财务目录 %d 个 parquet' % len(files))
print()

print('=' * 100)
print('26 个字段定位表（值 = 该表命中）')
print('=' * 100)
missing = []
for fld in FIN_FIELDS:
    hits = []
    for f, (names, sch, nr) in sorted(schema_map.items()):
        if fld in names:
            tp = sch.field(fld).type
            nn = None
            hits.append('%s[%s]' % (f[:-8], str(tp)[:14]))
    if not hits:
        missing.append(fld)
        print('  %-22s *** 全库缺失 ***' % fld)
    else:
        print('  %-22s %s' % (fld, '  '.join(hits)))
print()
print('全库缺失字段 %d 个: %s' % (len(missing), missing))
print()

print('=' * 100)
print('各表 q_ 前缀列清单')
print('=' * 100)
for f, (names, sch, nr) in sorted(schema_map.items()):
    qs = sorted(n for n in names if n.startswith('q_'))
    if qs:
        print('  %-22s 行数=%-8d q_ 列 %d 个' % (f, nr, len(qs)))
        print('      %s' % qs)
print()
print('=' * 100)
print('全库字段总数（供 run_screen 的 keep 列表覆盖率判断）')
print('=' * 100)
allf = set()
for f, (names, sch, nr) in schema_map.items():
    allf |= names
print('  全库唯一字段名 %d 个' % len(allf))
cov = sum(1 for f in FIN_FIELDS if f in allf)
print('  run_screen FIN_FIELDS 命中 %d/%d' % (cov, len(FIN_FIELDS)))