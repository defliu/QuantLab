# coding=utf-8
"""财务表键唯一性与 LOGIC KEY 覆盖审计（只读）。

要回答三个问题：
  Q1 前面覆盖审计的算术差额去哪了？-> 是不是 D/E 侧存在重复 FULL KEY
  Q2 D 独有的那 2 / 414 条 FULL KEY，在 E 里有没有同 LOGIC KEY 的其他公告版本？
     （决定「业务对象是否真在 E 缺失」，还是只是公告版本不同）
  Q3 空键记录有多少？（PIT 过滤 ann_date 时会不会漏）
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock\finance'

FULL_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'cashflow': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'income': ['ts_code', 'end_date', 'report_type', 'ann_date'],
    'dividend': ['ts_code', 'end_date', 'ann_date', 'div_proc'],
    'express': ['ts_code', 'end_date', 'ann_date'],
    'fina_indicator': ['ts_code', 'end_date', 'ann_date'],
    'forecast': ['ts_code', 'end_date', 'ann_date', 'type'],
    'share_change_event': ['ts_code', 'change_date', 'change_reason'],
}
LOGIC_KEYS = {
    'balancesheet': ['ts_code', 'end_date', 'report_type'],
    'cashflow': ['ts_code', 'end_date', 'report_type'],
    'income': ['ts_code', 'end_date', 'report_type'],
    'dividend': ['ts_code', 'end_date'],
    'express': ['ts_code', 'end_date'],
    'fina_indicator': ['ts_code', 'end_date'],
    'forecast': ['ts_code', 'end_date'],
    'share_change_event': ['ts_code', 'change_date'],
}


def load_keys(path, keys):
    f = pq.ParquetFile(path)
    have = set(f.schema_arrow.names)
    idx = []
    try:
        idx = [n for n in (f.schema_arrow.names) ]
    except Exception:
        idx = []
    use = [k for k in keys if k in have]
    t = pq.read_table(path, columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    for k in keys:
        if k in t.columns and k.endswith('_date'):
            t[k] = pd.to_datetime(t[k], format='mixed', errors='coerce').dt.strftime('%Y%m%d')
        elif k in t.columns:
            t[k] = t[k].astype('string').fillna('<NA>')
    return t


def keysets(t, keys):
    return set(map(tuple, t[keys].values))


print('=' * 100)
print('Q1) 各财务表 FULL KEY 唯一性（行数 vs 唯一键数）')
print('=' * 100)
print('%-22s %10s %10s %8s | %10s %10s %8s'
      % ('表', 'E行数', 'E唯一键', 'E重复', 'D行数', 'D唯一键', 'D重复'))
print('-' * 100)
store = {}
for name, fk in FULL_KEYS.items():
    ep = os.path.join(E_ROOT, name + '.parquet')
    dp = os.path.join(D_ROOT, name + '.parquet')
    if not (os.path.exists(ep) and os.path.exists(dp)):
        print('%-22s 文件缺失 E=%s D=%s' % (name, os.path.exists(ep), os.path.exists(dp)))
        continue
    et = load_keys(ep, fk)
    dt = load_keys(dp, fk)
    store[name] = (et, dt)
    es = keysets(et, fk)
    ds = keysets(dt, fk)
    print('%-22s %10d %10d %8d | %10d %10d %8d'
          % (name, len(et), len(es), len(et) - len(es),
             len(dt), len(ds), len(dt) - len(ds)))

print()
print('=' * 100)
print('Q2) D 独有 FULL KEY 在 E 是否有同 LOGIC KEY 的其他公告版本')
print('=' * 100)
for name in ('fina_indicator', 'forecast'):
    if name not in store:
        continue
    et, dt = store[name]
    fk = FULL_KEYS[name]
    lk = LOGIC_KEYS[name]
    es = keysets(et, fk)
    ds = keysets(dt, fk)
    only_d = ds - es
    print()
    print('--- %s ---' % name)
    print('D 独有 FULL KEY %d 条' % len(only_d))
    if not only_d:
        continue
    # 这些 D 独有行
    mask = dt[list(fk)].apply(tuple, axis=1).isin(only_d)
    sub = dt[mask]
    # 它们对应的 LOGIC KEY
    dlg = set(map(tuple, sub[lk].values))
    elg = keysets(et, lk)
    covered = dlg & elg
    print('  这些行的 LOGIC KEY 数: %d' % len(dlg))
    print('  其中 E 侧存在同 LOGIC KEY（别的公告版本）: %d' % len(covered))
    print('  E 侧完全没有该业务对象: %d' % len(dlg - elg))
    # 逻辑键完全缺失的业务对象明细
    miss = dlg - elg
    if miss:
        print('  >> E 缺失的业务对象明细（LOGIC KEY 层面）:')
        mm = sub[sub[lk].apply(tuple, axis=1).isin(miss)]
        for r in mm[lk].drop_duplicates().itertuples(index=False):
            print('     %s' % (' | '.join(str(x) for x in r),))
    # 公告版本对比
    if covered:
        print('  >> 已覆盖对象的 E 侧公告版本（示例前 5 组）:')
        n = 0
        for lg in list(covered)[:5]:
            e_rows = et[et[lk].apply(tuple, axis=1) == lg]
            d_rows = sub[sub[lk].apply(tuple, axis=1) == lg]
            print('     LOGIC %s' % (' | '.join(str(x) for x in lg),))
            print('       E ann_date: %s' % sorted(set(e_rows['ann_date'].astype(str))))
            print('       D ann_date: %s' % sorted(set(d_rows['ann_date'].astype(str))))
            n += 1
            if n >= 5:
                break

print()
print('=' * 100)
print('Q3) 空键记录（PIT 过滤 ann_date 时的影响）')
print('=' * 100)
for name, fk in FULL_KEYS.items():
    if name not in store:
        continue
    et, dt = store[name]
    print()
    print('--- %s ---' % name)
    for tag, t in (('E', et), ('D', dt)):
        parts = []
        for k in fk:
            if k.endswith('_date'):
                n_null = int(t[k].isna().sum())
            else:
                n_null = int((t[k].astype('string').fillna('<NA>') == '<NA>').sum())
            parts.append('%s空=%d' % (k, n_null))
        any_null = 0
        m = pd.Series(False, index=t.index)
        for k in fk:
            if k.endswith('_date'):
                m |= t[k].isna()
            else:
                m |= (t[k].astype('string').fillna('<NA>') == '<NA>')
        any_null = int(m.sum())
        print('  [%s] %s  含任一空键=%d' % (tag, ' '.join(parts), any_null))
