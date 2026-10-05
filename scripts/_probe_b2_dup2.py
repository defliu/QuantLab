# coding=utf-8
"""财务表重复键性质分析（只读，内存友好）。

Q1: 重复键的“组内倍数分布”是多少？（1 组 2 行，还是 1 组 50 行）
Q2: 同键多行是“内容完全相同的冗余”，还是“同键不同值的多个版本”？
    -> 若是后者，整体替换会把多版本一起灌进生产，改变 PIT 语义
Q3: 每张表的重复集中在哪些报告期？（能否定位供应商的推送特征）
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'

FULL_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'share_change_event': ['ts_code', 'change_date', 'change_reason'],
}


def read(path, columns=None):
    f = pq.ParquetFile(path)
    allc = list(f.schema_arrow.names)
    use = columns if columns else allc
    use = [c for c in use if c in allc]
    t = pq.read_table(path, columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    return t, allc


print('=' * 100)
print('Q1/Q2) 重复键性质：冗余 vs 多版本（逐表）')
print('=' * 100)

for name in ('fina_indicator', 'balancesheet', 'cashflow', 'income', 'forecast',
             'dividend', 'share_change_event'):
    fk = FULL_KEYS[name]
    path = os.path.join(E_ROOT, name + '.parquet')
    t, allc = read(path)
    valcols = [c for c in t.columns if c not in fk]

    g = t.groupby(fk, dropna=False, sort=False)
    size = g.size()
    n_groups = len(size)
    n_rows = len(t)
    dup_groups = size[size > 1]

    print()
    print('=' * 100)
    print('%s  行数=%d 唯一键组=%d 重复组=%d 冗余行=%d'
          % (name, n_rows, n_groups, len(dup_groups), n_rows - n_groups))
    print('-' * 100)
    if len(dup_groups) == 0:
        print('  无重复键')
        continue
    print('  组内倍数分布（倍数: 组数）:')
    dist = dup_groups.value_counts().sort_index()
    for mult, cnt in dist.head(12).items():
        print('    %dx : %d 组  (占重复组 %.1f%%)' % (mult, cnt, 100.0 * cnt / len(dup_groups)))
    if len(dist) > 12:
        print('    ... 另有 %d 种倍数' % (len(dist) - 12))
    print('  最大组倍数 = %d' % dup_groups.max())

    # 取重复最多的 2 组，检查组内是否内容一致
    worst = dup_groups.sort_values(ascending=False).head(2).index
    for wkey in worst:
        sub = g.get_group(wkey)
        same = sub[valcols].astype('string').fillna('<NA>').nunique(dropna=False)
        diff_cols = same[same > 1]
        print('  >> 组 %s 行数=%d' % (str(wkey)[:96], len(sub)))
        if len(diff_cols) == 0:
            print('       组内所有值列完全一致 -> 纯冗余重复行')
        else:
            print('       组内 %d 个值列存在差异 -> 同一 FULL KEY 的多个版本！'
                  % len(diff_cols))
            print('       差异列(前10): %s' % list(diff_cols.index[:10]))
            # 打印前几行差异值
            for c in list(diff_cols.index[:4]):
                vals = sub[[fk[0], fk[1], c]].copy()
                vals = vals.drop_duplicates()
                print('         %s 的组内取值: %s'
                      % (c, list(vals[c].astype(str).head(4))))

    del t, g, size
