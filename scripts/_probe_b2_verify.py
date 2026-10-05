# coding=utf-8
"""第二批阻塞点复核（修正版）。

修正上一版 _probe_b2_gap.py 的 C 段错误：
  E 侧 675628 行只对应 375997 个 (ts_code,end_date) 唯一键，直接按
  (ts_code,end_date) merge 会产生笛卡尔膨胀，导致 ej / dj 行数不同，
  再按位置比较 q_* 数值就是「拿 A 股票的数跟 B 股票比」——结论全废。
  本版在 merge 前先各自去重到唯一键，并断言两侧行数相等。

同时重跑 Q1 的 26 字段填充率（此前 _audit_batch2.json 可能已过期）。

只读，不写生产文件。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'
EP = os.path.join(E_ROOT, 'fina_indicator.parquet')
DP = os.path.join(D_FIN, 'fina_indicator.parquet')

FIN_FIELDS = [
    'bps', 'roe', 'gross_margin', 'netprofit_margin', 'ocfps', 'eps',
    'current_ratio', 'quick_ratio', 'debt_to_assets', 'profit_dedt',
    'netprofit_yoy', 'dt_netprofit_yoy', 'or_yoy', 'ocf_yoy', 'roe_yoy',
    'assets_yoy', 'tr_yoy', 'q_roe', 'q_gsprofit_margin',
    'q_netprofit_margin', 'q_sales_qoq', 'q_netprofit_qoq',
    'q_dtprofit_to_profit', 'q_ocf_to_sales', 'dtprofit_to_profit',
    'ocf_to_profit', 'ocf_to_or',
]

ef = pq.ParquetFile(EP)
df_ = pq.ParquetFile(DP)
en = set(ef.schema_arrow.names)
dn = set(df_.schema_arrow.names)
present = [c for c in FIN_FIELDS if c in en and c in dn]
print('=' * 92)
print('Q1) 26 字段填充率（E=%d 行 / D=%d 行）' % (ef.metadata.num_rows,
                                           df_.metadata.num_rows))
print('=' * 92)
print('%-24s %12s %12s %12s' % ('字段', 'E填充率', 'D填充率', '判定'))
et = ef.read(columns=['ts_code'] + present).to_pandas()
dt = df_.read(columns=['ts_code'] + present).to_pandas()
er, dr = len(et), len(dt)
crit = []
for c in present:
    pe = 100.0 * et[c].notna().sum() / max(er, 1)
    pd_ = 100.0 * dt[c].notna().sum() / max(dr, 1)
    if pe < pd_ * 0.5:
        flag = 'E 显著缺失'
    elif pe > pd_ * 1.5:
        flag = 'E 更多'
    else:
        flag = '相当'
    if flag != '相当':
        crit.append((c, pe, pd_, flag))
    print('%-24s %11.2f%% %11.2f%%  %s' % (c, pe, pd_, flag))
print()
print('差异字段 %d 个:' % len(crit))
for c, pe, pd_, f in crit:
    print('   %-24s E=%.2f%%  D=%.2f%%  %s' % (c, pe, pd_, f))
del et, dt

print()
print('=' * 92)
print('C) 重叠键上 E vs D 的 q_* 数值一致性（去重后逐键对齐）')
print('=' * 92)
Q = ['q_roe', 'q_gsprofit_margin', 'q_netprofit_margin', 'q_sales_qoq',
     'q_netprofit_qoq', 'q_dtprofit_to_profit', 'q_ocf_to_sales']
KEYS = ['ts_code', 'end_date']


def uniq(path, cols):
    """读表 -> 只保留 keys+cols -> 按 keys 去重（取首个）-> 返回。"""
    t = pq.ParquetFile(path).read(columns=KEYS + cols).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    return t.drop_duplicates(subset=KEYS, keep='first')


e2 = uniq(EP, Q)
d2 = uniq(DP, Q)
print('E 唯一键 %d   D 唯一键 %d' % (len(e2), len(d2)))
assert not e2.duplicated(subset=KEYS).any(), 'E 去重失败'
assert not d2.duplicated(subset=KEYS).any(), 'D 去重失败'

mg = e2.merge(d2, on=KEYS, how='inner', suffixes=('_e', '_d'))
print('重叠键 %d   E独有 %d   D独有 %d'
      % (len(mg), len(e2) - len(mg), len(d2) - len(mg)))
print()
print('%-24s %10s %10s %14s %14s %10s'
      % ('字段', '重叠非空', 'E非空', '一致(<1%)', '中位相对差', 'D非空'))
for c in Q:
    a = pd.to_numeric(mg[c + '_e'], errors='coerce')
    b = pd.to_numeric(mg[c + '_d'], errors='coerce')
    ne = int(a.notna().sum())
    nd = int(b.notna().sum())
    both = a.notna() & b.notna()
    nb = int(both.sum())
    if nb < 200:
        print('%-24s %10d %10d %14s %14s %10d   样本不足'
              % (c, nb, ne, '-', '-', nd))
        continue
    av, bv = a[both], b[both]
    big = bv.abs() > 1e-6
    if big.sum() < 200:
        print('%-24s %10d %10d %14s %14s %10d   分母过小'
              % (c, nb, ne, '-', '-', nd))
        continue
    rel = ((av - bv).abs() / bv.abs())[big]
    print('%-24s %10d %10d %13.4f %14.3e %10d'
          % (c, nb, ne, float((rel < 0.01).mean()), float(rel.median()), nd))