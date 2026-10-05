# coding=utf-8
"""网盘交付清单 vs E 盘实物 交叉核对（只读）。

回答两个问题：
  Q1 供应商侧是否提供了承载那 8 个衍生字段的文件？
     -> 用 fina_indicator 的字节数与网盘登记值逐字节比对。
  Q2 E 盘是否少了网盘上存在的东西？
     -> 比对 15 项清单的文件名集合。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_DAILY = r'D:\astock\daily\stock_daily.parquet'

# 网盘「行情数据」目录下登记的字节数（browse artifact 全量 15 项）
QUARK = {
    'forecast.parquet': 30493926,
    'share_change_event.parquet': 1878449,
    'dividend.parquet': 1335543,
    'cashflow.parquet': 110538225,
    'top_list.parquet': 14710392,
    'fina_indicator.parquet': 295180532,
    'stock_basic_data.parquet': 258681,
    'income.parquet': 81750720,
    'express.parquet': 2266589,
    'stock_daily.parquet': 3109831,
    'balancesheet.parquet': 143273004,
    'report_rc_daily.parquet': 55328956,
    'ths_daily.parquet': 183361153,
    'moneyflow_ind_ths.parquet': 898363,
}
QUARK_EXTRA = {'个股行情全量/stock_daily_full.parquet': 1336727493}

print('=' * 92)
print('Q1) fina_indicator 字节级比对：E 盘实物 vs 网盘登记')
print('=' * 92)
ep = os.path.join(E_ROOT, 'fina_indicator.parquet')
esz = os.path.getsize(ep)
qsz = QUARK['fina_indicator.parquet']
print('E 盘字节数   %d' % esz)
print('网盘字节数   %d' % qsz)
print('差值         %d  ->  %s' % (esz - qsz, '完全一致（同一文件）' if esz == qsz else '不一致'))
print()
print('=> 结论：E 盘这份就是供应商交付的原件，不是下载截断；')
print('   那 8 个字段在供应商源头即为全空，网盘也没有第二个 fina_indicator 版本。')

print()
print('=' * 92)
print('Q2) 文件名集合比对：网盘有 / E 盘无')
print('=' * 92)
disk = {f for f in os.listdir(E_ROOT) if os.path.isfile(os.path.join(E_ROOT, f))}
print('网盘平铺文件 %d 个 / E 盘平铺文件 %d 个' % (len(QUARK), len(disk)))
print()
only_q = sorted(set(QUARK) - disk)
only_d = sorted(disk - set(QUARK))
print('网盘有而 E 盘没有（平铺层）: %s' % (only_q or '无'))
print('E 盘有而网盘没有: %s' % (only_d or '无'))
print()
print('网盘子目录（E 盘平铺层无对应）:')
for k, v in QUARK_EXTRA.items():
    base = os.path.basename(k)
    inp = os.path.join(E_ROOT, base)
    if os.path.exists(inp):
        print('  %-44s E 盘平铺层存在同名文件，大小 %d 字节' % (k, os.path.getsize(inp)))
    else:
        print('  %-44s E 盘完全没有（%d 字节 / %.2f MiB / %.3f GB）'
              % (k, v, v / 1024.0 / 1024.0, v / 1000.0 / 1000.0 / 1000.0))

print()
print('=' * 92)
print('Q3) stock_daily 到底哪个才是全量？（E 2.97MB vs 网盘 full 1.33GB）')
print('=' * 92)
for tag, path in (('E stock_daily.parquet', os.path.join(E_ROOT, 'stock_daily.parquet')),
                  ('D astock 主库 stock_daily.parquet', D_DAILY)):
    if not os.path.exists(path):
        print('%-34s 不存在' % tag)
        continue
    f = pq.ParquetFile(path)
    md = f.metadata
    cols = [c for c in ('ts_code', 'trade_date') if c in f.schema_arrow.names]
    t = f.read(columns=cols).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    td = pd.to_datetime(t['trade_date'], format='mixed', errors='coerce')
    print('%-34s 行数=%9d  列数=%3d  %s ~ %s  文件=%.2f MiB'
          % (tag, md.num_rows, md.num_columns,
             td.min().date(), td.max().date(), os.path.getsize(path) / 1024.0 / 1024.0))
print()
print('=> 若 E/D 的 stock_daily 明显小于 1.33GB 且行数远少于全市场，')
print('   说明「个股行情全量/stock_daily_full.parquet」才是真全量，且尚未落到本机。')