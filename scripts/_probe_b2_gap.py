# coding=utf-8
"""第二批阻塞点定性：E vs D 财务表的覆盖与 q_* 填充缺口到底出在哪。

只读，不写生产文件。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

FIN_ORDER = ['balancesheet', 'cashflow', 'dividend', 'express',
             'fina_indicator', 'forecast', 'income', 'share_change_event']

Q_FIELDS = ['q_roe', 'q_gsprofit_margin', 'q_netprofit_margin',
            'q_sales_qoq', 'q_netprofit_qoq', 'q_dtprofit_to_profit',
            'q_ocf_to_sales']


def keys_of(tbl, avail):
    for k in (['ts_code', 'end_date', 'report_type'],
              ['ts_code', 'end_date'], ['ts_code', 'change_date']):
        if all(c in avail for c in k):
            return k
    return ['ts_code']


def datecol(df):
    """取该表实际存在的日期列并转 datetime，缺失返回全 NaT。"""
    for c in ('end_date', 'change_date'):
        if c in df.columns:
            return pd.to_datetime(df[c], format='mixed', errors='coerce')
    return pd.Series(pd.NaT, index=df.index, dtype='datetime64[ns]')


print('=' * 92)
print('A) 8 张财务表：E vs D 行数 / 时间覆盖 / 主键去重后基数')
print('=' * 92)
print('%-20s %10s %10s %12s %12s %12s %12s'
      % ('表', 'E行', 'D行', 'E最早', 'E最晚', 'D最早', 'D最晚'))
info = {}
for t in FIN_ORDER:
    ep = os.path.join(E_ROOT, t + '.parquet')
    dp = os.path.join(D_FIN, t + '.parquet')
    if not os.path.exists(ep):
        print('%-20s  E 侧文件不存在' % t)
        continue
    ef = pq.ParquetFile(ep)
    df_ = pq.ParquetFile(dp)
    ec = [c for c in ['ts_code', 'end_date', 'change_date'] if c in ef.schema_arrow.names]
    ed = ef.read(columns=ec).to_pandas()
    dd = df_.read(columns=ec).to_pandas()
    for f in (ed, dd):
        if isinstance(f.index, pd.MultiIndex):
            f.reset_index(inplace=True)
    es = datecol(ed)
    ds = datecol(dd)
    info[t] = (len(ed), len(dd))
    print('%-20s %10d %10d %12s %12s %12s %12s'
          % (t, len(ed), len(dd),
             es.min().date() if es.notna().any() else '-',
             es.max().date() if es.notna().any() else '-',
             ds.min().date() if ds.notna().any() else '-',
             ds.max().date() if ds.notna().any() else '-'))

print()
print('=' * 92)
print('B) fina_indicator 的 q_* 填充缺口：是「历史没覆盖」还是「全期都缺」')
print('=' * 92)
ep = os.path.join(E_ROOT, 'fina_indicator.parquet')
dp = os.path.join(D_FIN, 'fina_indicator.parquet')
en = set(pq.ParquetFile(ep).schema_arrow.names)
dn = set(pq.ParquetFile(dp).schema_arrow.names)
have = [c for c in Q_FIELDS if c in en]
print('E 侧存在的 q_ 字段: %s' % have)
miss = [c for c in Q_FIELDS if c not in en]
if miss:
    print('E 侧不存在的 q_ 字段: %s' % miss)

cols = ['ts_code', 'end_date'] + have
et = pq.ParquetFile(ep).read(columns=cols).to_pandas()
dt = pq.ParquetFile(dp).read(columns=cols).to_pandas()
for f in (et, dt):
    if isinstance(f.index, pd.MultiIndex):
        f.reset_index(inplace=True)
et['_y'] = pd.to_datetime(et['end_date'], format='mixed', errors='coerce').dt.year
dt['_y'] = pd.to_datetime(dt['end_date'], format='mixed', errors='coerce').dt.year

print()
print('%-8s %10s %10s %10s | %s' % ('年', 'E行数', 'E有q行', 'E占比', '各字段 E 填充率'))
for y in sorted(et['_y'].dropna().unique()):
    sub = et[et['_y'] == y]
    n = len(sub)
    row = ['%-8d %10d %10d %9.2f%%' % (y, n, int(sub[have].notna().any(axis=1).sum()),
                                       100.0 * sub[have].notna().any(axis=1).sum() / max(n, 1))]
    for c in have:
        row.append('%s=%.1f%%' % (c.replace('q_', ''),
                                  100.0 * sub[c].notna().sum() / max(n, 1)))
    print('  '.join(row))

print()
print('=' * 92)
print('C) 重叠键上 E 与 D 的 q_* 数值是否一致（决定「E 口径是否变了」）')
print('=' * 92)
ek = et[['ts_code', 'end_date']].drop_duplicates()
dk = dt[['ts_code', 'end_date']].drop_duplicates()
ov = ek.merge(dk, on=['ts_code', 'end_date'], how='inner')
print('E 唯一季度键 %d   D 唯一季度键 %d   重叠 %d'
      % (len(ek), len(dk), len(ov)))
print('E 独有 %d   D 独有 %d' % (len(ek) - len(ov), len(dk) - len(ov)))

ej = ov.merge(et, on=['ts_code', 'end_date'], how='left')
dj = ov.merge(dt, on=['ts_code', 'end_date'], how='left')
for c in have:
    a = pd.to_numeric(ej[c], errors='coerce')
    b = pd.to_numeric(dj[c], errors='coerce')
    both = a.notna() & b.notna()
    if both.sum() < 100:
        print('  %-22s 重叠非空样本不足 (%d)' % (c, int(both.sum())))
        continue
    rel = (a[both] - b[both]).abs() / b[both].abs().clip(lower=1e-9)
    rel = rel[b[both].abs() > 1e-6]
    print('  %-22s 重叠非空 %7d  一致(<1%%)=%.4f  中位相对差=%.3e'
          % (c, int(both.sum()), float((rel < 0.01).mean()) if len(rel) else float('nan'),
             float(rel.median()) if len(rel) else float('nan')))