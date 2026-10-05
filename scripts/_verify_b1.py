# coding=utf-8
"""第一批迁移独立复核：直接查盘，不信脚本自报。"""
import os
import json
import pandas as pd
import pyarrow.parquet as pq

ROOT = r'D:\astock'

print('=' * 70)
print('一、事务日志终态')
txn = json.load(open(os.path.join(ROOT, '_migration_txn_v2.json'), encoding='utf-8'))
print('run_tag=%s  finished=%s' % (txn.get('run_tag'), txn.get('finished')))
print('applied=%d  incr=%d  aborted=%s'
      % (len(txn.get('applied', [])), len(txn.get('incr', [])),
         txn.get('aborted')))
for e in txn.get('applied', []):
    print('   %-34s phase=%-26s rows=%s'
          % (e['name'], e.get('phase'), e.get('rows')))
print('残留 rollback_incomplete: %s' % txn.get('rollback_incomplete'))

print('=' * 70)
print('二、INCR 归档')
d = os.path.join(ROOT, '_incremental', 'daily')
for f in sorted(os.listdir(d)):
    p = os.path.join(d, f)
    t = pq.read_table(p)
    print('   %s  行=%d 列=%d  大小=%d'
          % (f, t.num_rows, t.num_columns, os.path.getsize(p)))

print('=' * 70)
print('三、主库日线')
p = os.path.join(ROOT, 'daily', 'stock_daily.parquet')
pf = pq.ParquetFile(p)
print('   行数=%d  列数=%d  大小=%d  mtime=%s'
      % (pf.metadata.num_rows, pf.metadata.num_columns,
         os.path.getsize(p),
         __import__('time').strftime(
             '%Y-%m-%d %H:%M:%S',
             __import__('time').localtime(os.path.getmtime(p)))))
df = pd.read_parquet(p, columns=['trade_date', 'ts_code'])
# 日线以 (trade_date, ts_code) 作索引落盘，read_parquet 会恢复成
# MultiIndex，键列不在 columns 里 —— 必须先 reset_index 才能取到。
if isinstance(df.index, pd.MultiIndex):
    df = df.reset_index()
df['trade_date'] = pd.to_datetime(df['trade_date'])
print('   日期范围: %s ~ %s' % (df.trade_date.min().date(), df.trade_date.max().date()))
print('   主键唯一: %s' % (not df.duplicated(['trade_date', 'ts_code']).any()))
for day in ['20260629', '20260630', '20260701', '20260702', '20260703']:
    n = int((df.trade_date == pd.Timestamp(day)).sum())
    print('   补洞日 %s -> %d 行  %s'
          % (day, n, 'OK' if n > 5000 else '!! 异常'))

print('=' * 70)
print('四、其余 5 张 FULL 表')
for rel, exp in [('lhb/top_list.parquet', 230099),
                 ('research/report_rc_daily.parquet', 936424),
                 ('board/ths_daily.parquet', 3018169),
                 ('board_fundflow/moneyflow_ind_ths.parquet', 44370),
                 ('basic/stock_basic.parquet', 5913)]:
    p = os.path.join(ROOT, rel.replace('/', os.sep))
    m = pq.ParquetFile(p).metadata
    print('   %-44s 行=%-9d 期望=%-9d %s'
          % (rel, m.num_rows, exp, 'OK' if m.num_rows == exp else '!! 不符'))

print('=' * 70)
print('五、本轮备份文件')
for sub in ['daily', 'lhb', 'research', 'board', 'board_fundflow', 'basic']:
    dd = os.path.join(ROOT, sub)
    for f in sorted(os.listdir(dd)):
        if 'bak_20261003' in f:
            print('   %s/%s  %d bytes' % (sub, f, os.path.getsize(os.path.join(dd, f))))

print('=' * 70)
print('六、staging 残留检查')
found = []
for sub in os.listdir(ROOT):
    dd = os.path.join(ROOT, sub)
    if not os.path.isdir(dd):
        continue
    for f in os.listdir(dd):
        if '.staging_' in f or '.rollback_' in f:
            found.append(sub + '/' + f)
print('   残留: %s' % (found or '无'))