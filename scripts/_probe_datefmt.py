# -*- coding: utf-8 -*-
"""探查非财务表日期列的原始字符串形态（只读）。

目的：确认 canon_date 的 dateutil 慢路径残值到底是什么形态，
以便用向量化规则替代，而不是让它逐元素走 dateutil。
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
D_BASE = r'D:\astock'

TARGETS = [
    ('top_list', 'lhb', 'top_list.parquet', 'top_list.parquet', 'trade_date'),
    ('report_rc_daily', 'research', 'report_rc_daily.parquet',
     'report_rc_daily.parquet', 'report_date'),
    ('ths_daily', 'board', 'ths_daily.parquet', 'ths_daily.parquet', 'trade_date'),
    ('moneyflow_ind_ths', 'board_fundflow', 'moneyflow_ind_ths.parquet',
     'moneyflow_ind_ths.parquet', 'trade_date'),
    ('stock_basic', 'basic', 'stock_basic_data.parquet',
     'stock_basic.parquet', 'list_date'),
]


def say(*a):
    print(*a)
    sys.stdout.flush()


def read_col(path, col):
    f = pq.ParquetFile(path)
    if col not in f.schema_arrow.names:
        return None, 'ABSENT'
    typ = str(f.schema_arrow.field(col).type)
    df = pd.read_parquet(path, columns=[col])
    if col not in df.columns:
        df = df.reset_index()
        if col not in df.columns:
            return None, 'INDEX_ONLY'
    return df[col], typ


def profile(tag, s, typ):
    say('  [%s] arrow_type=%s  dtype=%s  n=%d' % (tag, typ, s.dtype, len(s)))
    if pd.api.types.is_datetime64_any_dtype(s):
        v = s.dropna()
        if len(v):
            say('    datetime64 -> %s ~ %s' % (v.min(), v.max()))
        return
    t = s.astype(str).str.strip()
    dg = t.str.replace(r'\D', '', regex=True)
    say('    纯数字长度分布: %s' % (dg.str.len().value_counts().head(6).to_dict(),))
    bad = t[(dg.str.len() != 8) | dg.isin(['nan', 'NaT', 'None', ''])]
    say('    非8位数字行数=%d' % len(bad))
    if len(bad):
        vc = bad.value_counts().head(8)
        for k, n in vc.items():
            say('      %-28r x%d' % (k, n))


def main():
    for name, sub, efile, dfile, col in TARGETS:
        say('')
        say('=' * 90)
        say('### %s  col=%s' % (name, col))
        for tag, p in (('D', os.path.join(D_BASE, sub, dfile)),
                       ('E', os.path.join(E_ROOT, efile))):
            if not os.path.exists(p):
                say('  [%s] 文件不存在 %s' % (tag, p))
                continue
            s, typ = read_col(p, col)
            if s is None:
                say('  [%s] 列不可读 (%s)' % (tag, typ))
                continue
            profile(tag, s, typ)
    say('')
    say('DATEFMT_DONE')


if __name__ == '__main__':
    main()
