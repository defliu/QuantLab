# -*- coding: utf-8 -*-
"""下钻验证：DESTRUCTED 列在 E 侧到底是真空还是存储/解析问题（只读）。

若 E 把这些列存成字符串 "None"/"-"/"--"，pyarrow 的 null_count 会是 0，
to_numeric 之后才变 NaN —— 那种情况属于可修复的解析问题，而非数据源缺失。

本脚本直接查 parquet 底层统计 + 抽样原始值。
"""
import os
import sys

import pyarrow.parquet as pq
import pandas as pd

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

E_ROOT = r'E:\temp\行情数据'
D_BASE = r'D:\astock'

CHECK = {
    'fina_indicator': ['ocf_to_or', 'ocf_to_profit', 'non_op_profit',
                       'q_profit_yoy', 'q_netprofit_yoy', 'roe', 'ocfps',
                       'bps', 'netprofit_margin'],
    'income': ['total_opcost', 'oth_income', 'total_cogs', 'operate_profit'],
    'balancesheet': ['lease_liab', 'use_right_assets', 'total_assets'],
}


def probe(tbl, cols):
    for tag, path in (('D', os.path.join(D_BASE, 'finance',
                                         tbl + '.parquet')),
                      ('E', os.path.join(E_ROOT, tbl + '.parquet'))):
        f = pq.ParquetFile(path)
        names = f.schema_arrow.names
        total = f.metadata.num_rows
        print()
        print('--- %s  %s   rows=%d' % (tbl, tag, total))
        for c in cols:
            if c not in names:
                print('    %-22s [无此列]' % c)
                continue
            i = names.index(c)
            st = f.metadata.row_group(0).column(i).statistics
            nulls = None
            if st is not None and st.null_count is not None:
                nulls = st.null_count
            typ = str(f.schema_arrow.field(c).type)
            # 底层 null_count 只覆盖 row_group(0)，标注清楚
            print('    %-22s type=%-14s rg0_nulls=%s'
                  % (c, typ, nulls))

    # 抽样：E 侧该列前若干非空原始值
    ep = os.path.join(E_ROOT, tbl + '.parquet')
    present = [c for c in cols if c in pq.ParquetFile(ep).schema_arrow.names]
    if present:
        s = pd.read_parquet(ep, columns=present)
        print('  [E 抽样原始值]')
        for c in present:
            col = s[c]
            nn = col.dropna()
            print('    %-22s 非空=%-8d dtype=%-10s 样本=%s'
                  % (c, len(nn), str(col.dtype),
                     list(nn.head(3).astype(str))[:3]))


def main():
    for tbl, cols in CHECK.items():
        probe(tbl, cols)
    print()
    print('RAW_NULL_DONE')


if __name__ == '__main__':
    main()