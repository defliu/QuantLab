# coding=utf-8
"""键列原始 dtype 体检（只读）。

疑点：不同脚本对同一张表算出的「唯一键数」互相矛盾
  _probe_b2_dup.py  : D fina_indicator 唯一键 338259（有 15928 重复）
  _probe_b2_pit.py  : D fina_indicator 354187 组且全是 1 行（无重复）
差异来源只能是「键归一化」。若日期列是整型（如 20250930），
pd.to_datetime(20250930, format='mixed') 会当成纳秒时间戳解析成 1970-01-01，
不同 end_date 全部塌缩到同一日期 -> 人为制造大量重复键。
本脚本打印键列的原始 arrow 类型与 pandas dtype，并对比两种归一化的唯一键数。
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
    ('dividend', ['ts_code', 'end_date', 'ann_date', 'div_proc']),
    ('express', ['ts_code', 'end_date', 'ann_date']),
    ('forecast', ['ts_code', 'end_date', 'ann_date', 'type']),
    ('share_change_event', ['ts_code', 'change_date', 'change_reason']),
]


def load(path, keys):
    f = pq.ParquetFile(path)
    arrow = {n: str(f.schema_arrow.field(n).type) for n in f.schema_arrow.names}
    use = [k for k in keys if k in arrow]
    t = pq.read_table(path, columns=use).to_pandas()
    if isinstance(t.index, pd.MultiIndex):
        t = t.reset_index()
    elif not isinstance(t.index, pd.RangeIndex):
        t = t.reset_index()
    ghost = [c for c in t.columns if str(c).startswith('__index_level_')]
    if ghost:
        t = t.drop(columns=ghost)
    return t, arrow


def count_unique(t, keys, mode):
    x = t.copy()
    for k in keys:
        if k not in x.columns:
            continue
        if mode == 'raw':
            pass
        elif mode == 'strfmt':
            if str(x[k].dtype).startswith('datetime'):
                x[k] = x[k].dt.strftime('%Y%m%d')
            else:
                x[k] = x[k].astype('string')
        x[k] = x[k].astype(object).where(x[k].notna(), '<NA>')
    # 用可哈希字符串避免 NaN!=NaN 造成的假唯一
    return len(set(map(tuple, x[keys].astype(str).values)))


print('=' * 104)
print('键列 dtype 与三种归一化下的唯一键数对比')
print('=' * 104)
print('%-20s %-6s %-22s %-18s %10s %10s %10s'
      % ('表', '侧', '键列 arrow 类型', 'pandas dtype', 'raw', 'strfmt', '行数'))
print('-' * 104)

for name, fk in TABLES:
    for tag, p in (('E', os.path.join(E_ROOT, name + '.parquet')),
                   ('D', os.path.join(D_ROOT, name + '.parquet'))):
        if not os.path.exists(p):
            continue
        t, arrow = load(p, fk)
        atypes = ' '.join('%s=%s' % (k, arrow.get(k, '?')) for k in fk)
        dtypes = ' '.join('%s=%s' % (k, t[k].dtype) for k in fk if k in t.columns)
        u_raw = count_unique(t, fk, 'raw')
        u_str = count_unique(t, fk, 'strfmt')
        flag = '' if u_raw == u_str else '  <<< 归一化改变了唯一性!'
        print('%-20s %-6s %-22s %-18s %10d %10d %10d%s'
              % (name, tag, atypes[:22], dtypes[:18], u_raw, u_str, len(t), flag))
        if flag:
            print('%-20s  完整 arrow: %s' % ('', atypes))
            print('%-20s  完整 dtype: %s' % ('', dtypes))
