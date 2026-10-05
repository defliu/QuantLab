# coding=utf-8
"""E/D 财务表双向键差异归因（只读）。

已证：
  1) D 侧 8 张财务表行数==唯一键；E 侧 6 张存在大量同键异值版本
  2) 键集合是双向差异：fina_indicator 去重后 E+39577 / D+17060
  3) 旧 coverage 脚本用 pd.to_datetime 归一化塌缩了键，其"D独有=2"结论作废

本脚本：
  A. 打印键列真实 dtype（确认两侧表示是否一致，排除表示差异）
  B. 按 LOGIC KEY（业务对象）而非 FULL KEY 比较，定位差异性质
  C. 抽样打印 D 独有键 / E 独有键，判断是「供应商漏发」还是「口径不同」
"""
import os
import sys

import pandas as pd
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

E_ROOT = r'E:\temp\行情数据'
D_ROOT = r'D:\astock\finance'

# (表, FULL_KEY 物理记录键, LOGIC_KEY 业务对象键)
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


def load_all(path, keys):
    f = pq.ParquetFile(path)
    arrow = {n: str(f.schema_arrow.field(n).type) for n in f.schema_arrow.names}
    t = f.read().to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    for k in keys:
        if k in t.columns:
            t[k] = t[k].astype(object).where(t[k].notna(), '<NA>')
    return t, arrow


def kset(df, keys):
    return set(map(tuple, df[keys].astype(str).values))


print('=' * 112)
print('A. 键列真实 dtype（确认 E/D 表示一致，排除「表示差异」伪信号）')
print('=' * 112)
for name, fk, lk in TABLES:
    for tag, root in (('E', E_ROOT), ('D', D_ROOT)):
        p = os.path.join(root, name + '.parquet')
        if not os.path.exists(p):
            continue
        t, arrow = load_all(p, fk)
        info = '  '.join('%s:%s/%s' % (k, arrow.get(k, '?'), t[k].dtype)
                         for k in fk if k in t.columns)
        print('%-20s %-2s %s' % (name, tag, info))
    print()

print('=' * 112)
print('B. FULL KEY 与 LOGIC KEY 两个口径下的双向差异')
print('=' * 112)
print('%-20s %10s %10s | %-22s | %-22s'
      % ('表', 'E唯一', 'D唯一', 'FULL KEY 差异', 'LOGIC KEY 差异'))
print('-' * 112)

detail = {}
for name, fk, lk in TABLES:
    pe, pdd = os.path.join(E_ROOT, name + '.parquet'), os.path.join(D_ROOT, name + '.parquet')
    if not (os.path.exists(pe) and os.path.exists(pdd)):
        continue
    e, _ = load_all(pe, fk + [k for k in lk if k not in fk])
    d, _ = load_all(pdd, fk + [k for k in lk if k not in fk])
    ef, df_ = kset(e, fk), kset(d, fk)
    el, dl = kset(e, lk), kset(d, lk)
    detail[name] = (e, d, fk, lk)

    def fmt(a, b):
        oe, od = len(a - b), len(b - a)
        if oe == 0 and od == 0:
            return '完全一致'
        return 'E独有%-7d D独有%-7d' % (oe, od)

    print('%-20s %10d %10d | %-22s | %-22s'
          % (name, len(ef), len(df_), fmt(ef, df_), fmt(el, dl)))

print()
print('=' * 112)
print('C. fina_indicator 抽样：D 独有键（供应商是否漏发）')
print('=' * 112)
e, d, fk, lk = detail['fina_indicator']
ef, df_ = kset(e, fk), kset(d, fk)
donly = sorted(df_ - ef)
eonly = sorted(ef - df_)
print('D 独有键 %d 个，E 独有键 %d 个' % (len(donly), len(eonly)))

if donly:
    print('\n[D 独有键] 前 8 条：')
    ds = set(donly)
    dd = d[[tuple(map(str, r)) in ds for r in d[fk].astype(str).values]]
    for _, r in dd.head(8).iterrows():
        print('   %s | %s | %s | q_roe=%s'
              % (r['ts_code'], r['end_date'], r['ann_date'], r.get('q_roe')))
    # 这些 D 独有键，其 end_date 落在什么区间？E 侧该股票的 end_date 最大值？
    print('\n  D 独有键的 end_date 分布（近 12 个报告期）：')
    vc = pd.Series([k[1] for k in donly]).value_counts().sort_index()
    for k, v in list(vc.items())[-12:]:
        print('     end_date=%s  %d 条' % (k, v))
    miss_codes = sorted(set(k[0] for k in donly))
    print('  涉及股票数: %d' % len(miss_codes))
    # E 侧是否这些股票完全缺失该报告期？
    sample_code = miss_codes[0]
    e_sub = e[e['ts_code'] == sample_code]
    print('\n  样本股 %s 在 E 侧的 end_date 覆盖：' % sample_code)
    print('     E: %s' % sorted(set(str(x) for x in e_sub['end_date']))[-6:])
    print('     D: %s' % sorted(set(str(x) for x in d[d['ts_code'] == sample_code]['end_date']))[-6:])

print('\n[E 独有键] 前 8 条：')
if eonly:
    es = set(eonly)
    de = e[[tuple(map(str, r)) in es for r in e[fk].astype(str).values]]
    for _, r in de.head(8).iterrows():
        print('   %s | %s | %s | q_roe=%s'
              % (r['ts_code'], r['end_date'], r['ann_date'], r.get('q_roe')))
    print('\n  E 独有键的 end_date 分布（近 12 个报告期）：')
    vc = pd.Series([k[1] for k in eonly]).value_counts().sort_index()
    for k, v in list(vc.items())[-12:]:
        print('     end_date=%s  %d 条' % (k, v))
    print('  涉及股票数: %d' % len(set(k[0] for k in eonly)))
