# -*- coding: utf-8 -*-
"""
_probe_e_dupcause.py
====================
只读探针：查清 E 盘财务表「内部重复键」的成因，判断 E 是否为未去重原始版。
问题：E 财务表按 Tushare 口径主键算出的内部重复数极高
      （fina_indicator 675628 行 / 298924 重复键），D 侧同口径仅 1.5w。
      若直接整体替换，会把大量重复行灌入主库。

本探针回答：
  Q1  E 多出的 __index_level_0__ 列是什么（全空？是残留 index？）
  Q2  重复键组内到底是「哪些列不同」？是否存在可参与主键的判别列
      （update_flag / comp_type / f_ann_date / report_type 等）
  Q3  D 侧的重复是否同构（D 是否也已按同口径去重过）
  Q4  daily: E 的 trade_date dtype 与 D 的 index dtype 是否一致
  Q5  daily 增量是否为全量子集
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_FIN = r'D:\astock\finance'

FIN = [
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date']),
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
]

CANDIDATES = ['__index_level_0__', 'update_flag', 'comp_type', 'f_ann_date',
              'report_type', 'type', 'div_proc', 'end_date', 'ann_date', 'ts_code']

print('=' * 100)
print('E 财务表内部重复成因探针（只读）')
print('=' * 100)

for tbl, keys in FIN:
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    dp = os.path.join(D_FIN, tbl + '.parquet')
    print('')
    print('#' * 100)
    print('## %s   主键=%s' % (tbl, '+'.join(keys)))

    enames = list(pq.ParquetFile(ep).schema_arrow.names)
    dnames = list(pq.ParquetFile(dp).schema_arrow.names)
    print('   E 列数=%d  D 列数=%d' % (len(enames), len(dnames)))
    print('   E 独有列: %s' % ([c for c in enames if c not in set(dnames)]))
    print('   D 独有列: %s' % ([c for c in dnames if c not in set(enames)]))

    use = [c for c in CANDIDATES if c in enames or c in dnames]
    # __index_level_0__ 会被 pandas 当成 index 元数据恢复成 index，读 columns 时拿不到 -> 单独用 pyarrow 读
    read_cols = [c for c in use if c != '__index_level_0__']
    extra = [c for c in use if c not in keys]
    print('   可用判别列: %s' % extra)

    e = pd.read_parquet(ep, columns=read_cols)
    d = pd.read_parquet(dp, columns=[c for c in read_cols if c in dnames])

    if '__index_level_0__' in enames:
        c = pq.read_table(ep, columns=['__index_level_0__']).column('__index_level_0__').to_pandas()
        print('   [Q1] E.__index_level_0__: dtype=%s 非空=%d/%d 唯一值=%d 首值=%r'
              % (c.dtype, int(c.notna().sum()), len(c), c.nunique(dropna=True),
                 c.dropna().iloc[0] if c.notna().any() else None))

    for side, df in (('E', e), ('D', d)):
        dup_mask = df.duplicated(subset=keys, keep=False)
        n_dup_rows = int(dup_mask.sum())
        if not n_dup_rows:
            print('   [%s] 行数=%d  无重复键' % (side, len(df)))
            continue
        nun_g = df[dup_mask].groupby(keys, sort=False).size()
        print('   [%s] 行数=%d  重复涉及行=%d  重复组=%d  最大组=%d  纯冗余行=%d'
              % (side, len(df), n_dup_rows, len(nun_g), int(nun_g.max()),
                 len(df) - len(df.drop_duplicates(subset=keys))))

        sub = df[dup_mask]
        for c in [x for x in extra if x in df.columns]:
            nun = sub.groupby(keys, sort=False)[c].nunique(dropna=False)
            multi = int((nun > 1).sum())
            print('       - %-20s 组内有差异的组=%d/%d  => %s'
                  % (c, multi, len(nun), '可切开重复组' if multi > 0 else '无法区分(组内恒定)'))

        top_key = nun_g.index[0]
        mask = pd.Series(True, index=sub.index)
        for k, v in zip(keys, top_key):
            mask &= (sub[k] == v)
        grp = sub[mask]
        show_cols = [c for c in keys] + [c for c in extra if c not in keys and c in grp.columns]
        with pd.option_context('display.max_columns', 20, 'display.width', 250):
            print('       样本组(%s) 共%d行:' % ('+'.join('%s=%s' % (k, v) for k, v in zip(keys, top_key)), len(grp)))
            print(grp[show_cols].head(6).to_string(index=False))
        del sub, dup_mask, nun_g

    del e, d

print('')
print('#' * 100)
print('## Q4 daily dtype 契约（E 全量 vs D 主库）')
ef = pq.ParquetFile(os.path.join(E_ROOT, '个股行情全量', 'stock_daily_full.parquet'))
dfl = pq.ParquetFile(r'D:\astock\daily\stock_daily.parquet')
print('   E schema(trade_date/ts_code): %s / %s'
      % (ef.schema_arrow.field('trade_date').type, ef.schema_arrow.field('ts_code').type))
print('   D parquet index fields: %s'
      % [dfl.schema_arrow.field(i).name
         for i in range(len(dfl.schema_arrow.names))
         if dfl.schema_arrow.field(i).metadata])

e_head = pd.read_parquet(os.path.join(E_ROOT, '个股行情全量', 'stock_daily_full.parquet'),
                         columns=['trade_date', 'ts_code']).head(3)
print('   E pandas dtypes: trade_date=%s ts_code=%s' % (e_head['trade_date'].dtype, e_head['ts_code'].dtype))
print('   E head:\n%s' % e_head.to_string(index=False))

d_small = pd.read_parquet(r'D:\astock\daily\stock_daily.parquet', columns=['close']).head(3)
print('   D index names=%s level dtypes=%s'
      % (list(d_small.index.names),
         [d_small.index.get_level_values(i).dtype for i in range(d_small.index.nlevels)]))

print('')
print('## Q5 daily_incr 是否为 daily_full 子集（防重复计数）')
full_keys = pd.read_parquet(os.path.join(E_ROOT, '个股行情全量', 'stock_daily_full.parquet'),
                            columns=['trade_date', 'ts_code'])
incr_keys = pd.read_parquet(os.path.join(E_ROOT, 'stock_daily.parquet'),
                            columns=['trade_date', 'ts_code'])


def kh(df):
    s = df.copy()
    s['trade_date'] = s['trade_date'].astype(str).str.slice(0, 10).str.replace('-', '', regex=False)
    s['ts_code'] = s['ts_code'].astype(str)
    return set(pd.util.hash_pandas_object(s, index=False).tolist())


fk = kh(full_keys)
ik = kh(incr_keys)
print('   incr 行数=%d  已在 full 中=%d  不在 full 中=%d' % (len(ik), len(ik & fk), len(ik - fk)))
print('   incr 日期范围=%s~%s' % (str(incr_keys['trade_date'].min()), str(incr_keys['trade_date'].max())))
print('DUPCAUSE_DONE')