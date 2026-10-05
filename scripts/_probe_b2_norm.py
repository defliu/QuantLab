# coding=utf-8
"""E/D 财务表键差异归因（统一日期归一化版，只读）。

已证伪的旧结论：
  _probe_b2_coverage.py 用 pd.to_datetime(format='mixed') 归一化
  -> 把 '20260331'(str) 与 '2026-03-31 00:00:00'(datetime 字符串化) 混在同一列时判定错误
  -> 报出「D 独有 17060 / E 独有 39577」等伪差异

真实原因：D 库部分财务表日期列内部混用两种表示
  早期行  object '20260331'
  后期行  datetime64[ns] -> str 得到 '2026-03-31 00:00:00'
两者语义相同、字面不同。

本脚本把所有日期键列统一成 YYYYMMDD 字符串后重算键集合，
并报告 D 侧日期列的表示混杂程度。
"""
import os
import re
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock\finance'

TABLES = [
    ('fina_indicator', ['ts_code', 'end_date', 'ann_date'], ['ts_code', 'end_date']),
    ('balancesheet', ['ts_code', 'end_date', 'report_type', 'ann_date'], ['ts_code', 'end_date', 'report_type']),
    ('cashflow', ['ts_code', 'end_date', 'report_type', 'ann_date'], ['ts_code', 'end_date', 'report_type']),
    ('income', ['ts_code', 'end_date', 'report_type', 'ann_date'], ['ts_code', 'end_date', 'report_type']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type'], ['ts_code', 'end_date']),
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc'], ['ts_code', 'end_date']),
    ('express', ['ts_code', 'end_date', 'ann_date'], ['ts_code', 'end_date']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason'], ['ts_code', 'change_date']),
]

DATE_COLS = ('end_date', 'ann_date', 'change_date', 'f_ann_date')
_ISO = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')


def norm_date(v):
    """任意日期表示 -> YYYYMMDD 字符串；无法解析返回 '?'。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return '<NA>'
    s = str(v).strip()
    if not s or s in ('nan', 'NaT', 'None', '<NA>'):
        return '<NA>'
    m = _ISO.match(s)
    if m:
        return '%s%s%s' % m.groups()
    d = ''.join(ch for ch in s if ch.isdigit())
    if len(d) >= 8:
        return d[:8]
    return s


def load_all(path, keys):
    f = pq.ParquetFile(path)
    t = f.read().to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    for c in DATE_COLS:
        if c in t.columns:
            t[c] = t[c].map(norm_date)
    for k in keys:
        if k in t.columns and k not in DATE_COLS:
            t[k] = t[k].astype(object).where(t[k].notna(), '<NA>')
    return t


def kset(df, keys):
    return set(map(tuple, df[keys].astype(str).values))


print('=' * 112)
print('A. D 侧日期列表示混杂度（归一化前的原始形态计数）')
print('=' * 112)
print('%-20s %-3s %-12s %14s %14s %14s'
      % ('表', '侧', '列', 'YYYYMMDD', 'ISO带时间', '其他'))
print('-' * 112)
for name, fk, lk in TABLES:
    for tag, root in (('E', E_ROOT), ('D', D_ROOT)):
        p = os.path.join(root, name + '.parquet')
        if not os.path.exists(p):
            continue
        f = pq.ParquetFile(p)
        for c in [x for x in DATE_COLS if x in f.schema_arrow.names]:
            v = pq.read_table(p, columns=[c]).to_pandas()[c]
            a = sum(1 for x in v if re.match(r'^\d{8}$', str(x)))
            b = sum(1 for x in v if _ISO.match(str(x)))
            oth = len(v) - a - b
            if oth == 0 and b == 0:
                continue
            print('%-20s %-3s %-12s %14d %14d %14d%s'
                  % (name, tag, c, a, b, oth, '   <<< 混杂!' if (a and b) else ''))

print()
print('=' * 112)
print('B. 统一归一化后的真实键差异')
print('=' * 112)
print('%-20s %10s %10s %10s | %-26s | %-26s'
      % ('表', 'E行', 'E唯一', 'D唯一', 'FULL KEY 差异', 'LOGIC KEY 差异'))
print('-' * 112)

store = {}
for name, fk, lk in TABLES:
    pe = os.path.join(E_ROOT, name + '.parquet')
    pdd = os.path.join(D_ROOT, name + '.parquet')
    if not (os.path.exists(pe) and os.path.exists(pdd)):
        continue
    e = load_all(pe, fk)
    d = load_all(pdd, fk)
    ef, df_ = kset(e, fk), kset(d, fk)
    el, dl = kset(e, lk), kset(d, lk)
    store[name] = (e, d, fk, lk, ef, df_)

    def fmt(a, b):
        oe, od = len(a - b), len(b - a)
        if oe == 0 and od == 0:
            return '完全一致'
        if od == 0:
            return 'E 是 D 超集 (+%d)' % oe
        if oe == 0:
            return 'D 是 E 超集 (-%d)' % od
        return 'E独有%-8d D独有%-8d' % (oe, od)

    print('%-20s %10d %10d %10d | %-26s | %-26s'
          % (name, len(e), len(ef), len(df_), fmt(ef, df_), fmt(el, dl)))

print()
print('=' * 112)
print('C. E 侧重复键去重后 vs D：D 是否为 E 的超集（整体替换会不会丢数据）')
print('=' * 112)
print('%-20s %12s %12s %12s %10s'
      % ('表', 'E去重后', 'D唯一', 'D-E差', '判定'))
print('-' * 112)
for name, (e, d, fk, lk, ef, df_) in store.items():
    ef_dedup = ef  # 键集合本身与去重无关
    lost = len(df_ - ef_dedup)
    print('%-20s %12d %12d %12d %10s'
          % (name, len(ef_dedup), len(df_), len(df_) - len(ef_dedup),
             '安全(E超集)' if lost == 0 else '危险(丢%d键)' % lost))

print()
print('=' * 112)
print('D. fina_indicator 仍存在的 D 独有键（若上表判危险则此处为真实缺口）')
print('=' * 112)
e, d, fk, lk, ef, df_ = store['fina_indicator']
donly = sorted(df_ - ef)
if not donly:
    print('无 D 独有键 —— E 完全覆盖 D。')
else:
    print('D 独有 %d 个，前 10：' % len(donly))
    for kk in donly[:10]:
        print('   %s' % ' | '.join(map(str, kk)))
    print('涉及股票数 %d，报告期分布：' % len(set(k[0] for k in donly)))
    vc = pd.Series([k[1] for k in donly]).value_counts().sort_index()
    for k, v in list(vc.items())[-10:]:
        print('   end_date=%s  %d 条' % (k, v))
