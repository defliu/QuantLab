# coding=utf-8
"""E 侧财务表重复键性质定性（只读）。

已证事实：
  D 侧 8 张财务表 行数 == 唯一键（干净去重库）
  E 侧 fina_indicator 675628 行仅 376704 唯一键 → 298924 重复

本脚本回答一个决定性问题：E 侧这些重复是
  (A) 完全相同的行（供应商导出时物理重复，纯冗余）
  (B) 同键但内容不同（同一报告的多个公告/修正版本，有业务含义）
若是 (B)，整体替换会直接摧毁 PIT 公告版本结构，必须先定义版本取舍规则。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock\finance'

TABLES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]


def load_all(path):
    f = pq.ParquetFile(path)
    t = f.read().to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    return t


def norm_keys(t, keys):
    x = t.copy()
    for k in keys:
        if k in x.columns:
            x[k] = x[k].astype(object).where(x[k].notna(), '<NA>')
    return x


print('=' * 104)
print('E 侧财务表重复键性质：完全重复行(A-纯冗余) vs 同键异值(B-版本分歧)')
print('=' * 104)

for name, fk in TABLES:
    p = os.path.join(E_ROOT, name + '.parquet')
    if not os.path.exists(p):
        print('%-20s  <E 侧文件不存在>' % name)
        continue
    t = norm_keys(load_all(p), fk)
    n = len(t)
    val_cols = [c for c in t.columns if c not in fk]

    sizes = t.groupby(fk, dropna=False, sort=False).size()
    n_uniq = len(sizes)
    n_dupkeys = int((sizes > 1).sum())

    print('-' * 104)
    print('%-20s 行数=%-9d 唯一键=%-9d 重复键=%-8d 重复行=%d'
          % (name, n, n_uniq, n_dupkeys, n - n_uniq))
    if n_dupkeys == 0:
        print('%-20s -> 无重复' % '')
        continue

    dup_idx = sizes[sizes > 1].index
    sub = t.set_index(fk).loc[dup_idx]
    nun = sub.groupby(level=list(range(len(fk))), sort=False).nunique(dropna=False)
    mask = nun.gt(1).any(axis=0)
    diff_cols = [c for c in list(mask.index) if bool(mask[c])]

    if not diff_cols:
        print('%-20s -> A 纯冗余：重复键的所有数据列取值完全相同' % '')
    else:
        print('%-20s -> B 版本分歧：%d 个数据列在同键内取值不同' % ('', len(diff_cols)))
        print('%-24s 差异列: %s' % ('', ', '.join(map(str, diff_cols[:20]))))
        if len(diff_cols) > 20:
            print('%-24s ... 另有 %d 列' % ('', len(diff_cols) - 20))
    vc = sizes[sizes > 1].value_counts().sort_index()
    print('%-24s 每键行数分布: %s' % ('', {int(k): int(v) for k, v in vc.items()}))

print()
print('=' * 104)
print('D 侧复核：确认生产库无重复（对照）')
print('=' * 104)
for name, fk in TABLES:
    p = os.path.join(D_ROOT, name + '.parquet')
    if not os.path.exists(p):
        continue
    t = norm_keys(load_all(p), fk)
    sizes = t.groupby(fk, dropna=False, sort=False).size()
    print('%-20s 行数=%-9d 唯一键=%-9d 重复键=%d'
          % (name, len(t), len(sizes), int((sizes > 1).sum())))
