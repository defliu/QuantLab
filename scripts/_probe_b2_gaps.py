# coding=utf-8
"""两处真实缺口定性（只读）。

缺口1  fina_indicator: D 独有 2 键（A15130.SZ / A20005.SH @20260630）
缺口2  forecast:      D 独有 414 键（FULL KEY 口径；LOGIC KEY 下 E 是超集）

对每个缺口回答：这些记录在 E 侧是「完全不存在」还是「以另一种键形态存在」，
以及是否落在供应商覆盖范围（对照 stock_basic 的代码清单）。
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock\finance'


def norm_date(v):
    import re
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return '<NA>'
    s = str(v).strip()
    if not s or s in ('nan', 'NaT', 'None'):
        return '<NA>'
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return '%s%s%s' % m.groups()
    d = ''.join(ch for ch in s if ch.isdigit())
    return d[:8] if len(d) >= 8 else s


def load(path, dates):
    f = pq.ParquetFile(path)
    t = f.read().to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    for c in dates:
        if c in t.columns:
            t[c] = t[c].map(norm_date)
    return t


# ---------- 代码清单（供应商覆盖范围） ----------
basic = load(os.path.join(D_ROOT, '..', 'basic', 'stock_basic.parquet'),
             ['list_date', 'delist_date'])
e_basic = load(os.path.join(E_ROOT, 'stock_basic_data.parquet'),
               ['list_date', 'delist_date'])
d_codes = set(basic['ts_code'].astype(str))
e_codes = set(e_basic['ts_code'].astype(str))
print('=' * 100)
print('供应商覆盖范围（stock_basic）')
print('=' * 100)
print('  E 侧 stock_basic 代码数: %d' % len(e_codes))
print('  D 侧 stock_basic 代码数: %d' % len(d_codes))
print('  D 有 E 无: %d' % len(d_codes - e_codes))
if d_codes - e_codes:
    print('    例: %s' % sorted(d_codes - e_codes)[:15])

# ---------- 缺口 1: fina_indicator ----------
print()
print('=' * 100)
print('缺口1  fina_indicator：D 独有 2 键是否在 E 完全缺失')
print('=' * 100)
fk = ['ts_code', 'end_date', 'ann_date']
fe = load(os.path.join(E_ROOT, 'fina_indicator.parquet'), ['end_date', 'ann_date', 'f_ann_date'])
fd = load(os.path.join(D_ROOT, 'fina_indicator.parquet'), ['end_date', 'ann_date', 'f_ann_date'])

for code in ['A15130.SZ', 'A20005.SH']:
    de = fe[fe['ts_code'] == code]
    dd = fd[fd['ts_code'] == code]
    print('-' * 100)
    print('  %s  在 E 的 stock_basic 中: %s' % (code, code in e_codes))
    print('  E 侧 fina_indicator 行数=%d，D 侧=%d' % (len(de), len(dd)))
    print('  E 侧该股全部记录:')
    for _, r in de.iterrows():
        print('     end_date=%s ann_date=%s q_roe=%s' % (r['end_date'], r['ann_date'], r.get('q_roe')))
    print('  D 侧该股全部记录:')
    for _, r in dd.iterrows():
        print('     end_date=%s ann_date=%s q_roe=%s' % (r['end_date'], r['ann_date'], r.get('q_roe')))

# ---------- 缺口 2: forecast ----------
print()
print('=' * 100)
print('缺口2  forecast：D 独有 414 键（FULL KEY）')
print('=' * 100)
fk2 = ['ts_code', 'end_date', 'ann_date', 'type']
lk2 = ['ts_code', 'end_date']
fe2 = load(os.path.join(E_ROOT, 'forecast.parquet'), ['end_date', 'ann_date'])
fd2 = load(os.path.join(D_ROOT, 'forecast.parquet'), ['end_date', 'ann_date'])
ek = set(map(tuple, fe2[fk2].astype(str).values))
dk = set(map(tuple, fd2[fk2].astype(str).values))
donly = sorted(dk - ek)
print('  D 独有 FULL KEY: %d' % len(donly))
vc = pd.Series([k[0] for k in donly]).value_counts()
print('  涉及股票数: %d' % len(vc))
print('  是否在 E 的 stock_basic 中: %d/%d'
      % (sum(1 for c in vc.index if c in e_codes), len(vc)))

# 这些 D 独有键，其 LOGIC KEY 在 E 侧是否存在？
el = set(map(tuple, fe2[lk2].astype(str).values))
miss_logic = [k for k in donly if (k[0], k[1]) not in el]
print('  其中 LOGIC KEY(ts_code,end_date) 在 E 也不存在的: %d' % len(miss_logic))
print('    -> 说明 E 侧该报告期整段缺失（供应商漏发）')
if miss_logic:
    vc2 = pd.Series([k[1] for k in miss_logic]).value_counts().sort_index()
    print('    缺失报告期分布:')
    for k, v in list(vc2.items())[-12:]:
        print('       end_date=%s  %d 条' % (k, v))
    notin = [k[0] for k in miss_logic if k[0] not in e_codes]
    print('    其中不在 E stock_basic 的代码: %d 个 -> %s'
          % (len(set(notin)), sorted(set(notin))[:10]))

print()
print('  D 独有键前 10 条明细:')
for kk in donly[:10]:
    sub = fd2[(fd2['ts_code'] == kk[0]) & (fd2['end_date'] == kk[1])
              & (fd2['ann_date'] == kk[2]) & (fd2['type'] == kk[3])]
    for _, r in sub.iterrows():
        print('     %s | %s | %s | type=%s | p_change_min=%s'
              % (r['ts_code'], r['end_date'], r['ann_date'], r['type'], r.get('p_change_min')))
    se = fe2[(fe2['ts_code'] == kk[0]) & (fe2['end_date'] == kk[1])]
    if len(se):
        print('        E 侧同股票+报告期存在 %d 条: %s'
              % (len(se), [(x, y, z) for x, y, z in
                           se[['ann_date', 'type', 'p_change_min']].astype(str).values[:4]]))
    else:
        print('        E 侧同股票+报告期完全缺失')