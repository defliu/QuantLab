# coding=utf-8
"""E 侧财务「重复键」的本质判定 + PIT 风险量化（只读）。

核心问题：E 侧同一 FULL KEY 多行，f_ann_date 不同。
  FULL_KEY 里没有 f_ann_date，但 f_ann_date 才是「这批数据何时可知」的关键字段。
  若迁移时按 FULL_KEY 去重（keep first/last），就等于在 PIT 维度上随机选版本。

本脚本验证：
  Q1 E 侧重复是否呈「两个 index 簇」= 两批不同 dump 叠加
  Q2 D 生产库对同一 FULL KEY 保留了哪个 f_ann_date（最新？唯一？）
  Q3 若对 E 盲目 keep-last，与 D 的 PIT 版本是否冲突（未来函数风险量化）
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock\finance'

CASES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
]


def read(path, cols):
    f = pq.ParquetFile(path)
    allc = list(f.schema_arrow.names)
    use = [c for c in cols if c in allc]
    t = pq.read_table(path, columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    return t


for name, fk in CASES:
    print('=' * 100)
    print('%s   FULL_KEY=%s' % (name, fk))
    print('=' * 100)

    cols = fk + ['f_ann_date', 'update_flag', 'index']
    et = read(os.path.join(E_ROOT, name + '.parquet'), cols)
    dt = read(os.path.join(D_ROOT, name + '.parquet'), cols)

    for tag, t in (('E', et), ('D', dt)):
        if 'index' in t.columns:
            idx = pd.to_numeric(t['index'], errors='coerce')
            print('[%s] index 数值范围 %.0f ~ %.0f  中位 %.0f'
                  % (tag, idx.min(), idx.max(), idx.median()))
            # index 是否已经带序号含义（若是，重复可能是整行 offset）
            print('[%s] index 是否唯一: %s (唯一值 %d / 行数 %d)'
                  % (tag, idx.nunique() == len(t), idx.nunique(), len(t)))
        if 'update_flag' in t.columns:
            uf = t['update_flag'].astype('string').fillna('<NA>')
            print('[%s] update_flag 分布: %s'
                  % (tag, dict(uf.value_counts().head(5))))
        if 'f_ann_date' in t.columns:
            fa = pd.to_datetime(t['f_ann_date'], format='mixed', errors='coerce')
            print('[%s] f_ann_date 范围 %s ~ %s  空值 %d'
                  % (tag, fa.min().date(), fa.max().date(), int(fa.isna().sum())))
        print()

    # 同一 FULL KEY 组内 f_ann_date 是否真的不同
    print('  -- 同一 FULL KEY 组内 f_ann_date 是否不同 --')
    for tag, t in (('E', et), ('D', dt)):
        if 'f_ann_date' not in t.columns:
            continue
        g = t.groupby(fk, dropna=False)
        n = g['f_ann_date'].nunique(dropna=False)
        multi = n[n > 1]
        print('  [%s] 组数=%d  组内 f_ann_date 有多个的组=%d (%.1f%%)'
              % (tag, len(n), len(multi), 100.0 * len(multi) / max(len(n), 1)))

    # D 侧同一 FULL KEY 保留策略
    print()
    print('  -- D 生产库同一 FULL KEY 的行数分布 --')
    gd = dt.groupby(fk, dropna=False).size()
    print('  D 组内倍数分布: %s' % dict(gd.value_counts().sort_index().head(6)))

    # 量化 PIT 冲突：E 盲 keep-last vs D 现状
    print()
    print('  -- PIT 冲突量化（E keep-last vs D 现状） --')
    e_last = (et.sort_values('f_ann_date', na_position='first')
                .drop_duplicates(subset=fk, keep='last'))
    d_first = dt.drop_duplicates(subset=fk, keep='first')
    m = e_last[fk].merge(d_first[fk], on=fk, how='inner')
    j = e_last.set_index(fk)['f_ann_date'].to_frame('e_fa').join(
        d_first.set_index(fk)['f_ann_date'].to_frame('d_fa'), how='inner')
    ea = pd.to_datetime(j['e_fa'], format='mixed', errors='coerce')
    da = pd.to_datetime(j['d_fa'], format='mixed', errors='coerce')
    diff = ea != da
    print('  可比 FULL KEY=%d  其中 E(keep-last) 与 D 的 f_ann_date 不同=%d (%.2f%%)'
          % (len(j), int(diff.sum()), 100.0 * diff.sum() / max(len(j), 1)))
    fut = ea > da
    print('  其中 E 比 D 更晚（=更接近未来，潜在前视）=%d (%.2f%%)'
          % (int(fut.sum()), 100.0 * fut.sum() / max(len(j), 1)))
    if int(fut.sum()):
        ex = j[fut].head(5)
        print('  示例（E 更晚的行）:')
        for k, r in ex.iterrows():
            print('    %s  D_f_ann=%s  E_f_ann=%s'
                  % (str(k)[:78], r['d_fa'], r['e_fa']))
    print()
    del et, dt
