# -*- coding: utf-8 -*-
"""定位 E 侧 58 个近空列的填充规律：按 end_date 月份 / report_type / ann_date 年份分布。"""
import sys
import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8')

EP = r'E:\temp\行情数据\fina_indicator.parquet'
DP = r'D:\astock\finance\fina_indicator.parquet'


def read(p):
    t = pq.read_table(p)
    cols = [c for c in t.column_names if not c.startswith('__index_level_')]
    return t.select(cols).to_pandas()


e = read(EP)
d = read(DP)

probe = ['roe_avg', 'q_gr_yoy', 'invturn_days', 'op_to_debt', 'total_fa_trun', 'rd_exp']
print('=' * 78)
print('A) E 侧：这些列的非空行分布在哪些 end_date 月份？')
print('=' * 78)
mm = e['end_date'].astype(str).str[4:6]
for c in probe:
    nn = e[c].notna()
    print()
    print('--- %s  (E 非空 %d / %d)' % (c, int(nn.sum()), len(e)))
    sub = pd.DataFrame({'mm': mm[nn]}).value_counts().sort_index()
    print('    end_date 月份分布:', dict(sub))

print()
print('=' * 78)
print('B) D 侧同列对比')
print('=' * 78)
mmd = d['end_date'].astype(str).str[4:6]
for c in probe:
    nn = d[c].notna()
    sub = pd.DataFrame({'mm': mmd[nn]}).value_counts().sort_index()
    print('%-16s D 非空 %7d  月份分布 %s' % (c, int(nn.sum()), dict(sub)))

print()
print('=' * 78)
print('C) 是否与 ann_date 年份相关（E 是否只回填了部分年份）')
print('=' * 78)
yr = e['ann_date'].astype(str).str[:4]
for c in probe[:3]:
    nn = e[c].notna()
    sub = pd.DataFrame({'y': yr[nn]}).value_counts().sort_index()
    print('--- %s' % c)
    print('    ', dict(sub))

print()
print('=' * 78)
print('D) report_type 维度')
print('=' * 78)
if 'report_type' in e.columns:
    for c in probe[:3]:
        nn = e[c].notna()
        sub = pd.DataFrame({'rt': e.loc[nn, 'report_type'].astype(str)}).value_counts()
        print('%-16s E report_type 分布 %s' % (c, dict(sub)))
        nnd = d[c].notna()
        subd = pd.DataFrame({'rt': d.loc[nnd, 'report_type'].astype(str)}).value_counts()
        print('%-16s D report_type 分布 %s' % ('', dict(subd)))

print()
print('=' * 78)
print('E) E 侧整体：167 列里到底多少列是"全行填满"的？')
print('=' * 78)
n = len(e)
full = [c for c in e.columns if e[c].notna().sum() / n > 0.5]
near = [c for c in e.columns
        if 0 < e[c].notna().sum() / n <= 0.02]
print('E 列总数            :', len(e.columns))
print('E 填充率 >50% 的列  :', len(full))
print('E 填充率 <=2% 的列  :', len(near))
nd = len(d)
near_d = [c for c in d.columns if 0 < d[c].notna().sum() / nd <= 0.02]
print('D 列总数            :', len(d.columns))
print('D 填充率 <=2% 的列  :', len(near_d))
print('E 近空但 D 不近空的列数:', len(set(near) - set(near_d)))
