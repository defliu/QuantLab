# coding=utf-8
"""探测 q_* 反解所需表规模：行数、数值列数、物理行序是否已按 end_date 有序。

单季度差分对行序敏感（shift(1) 取物理上一行），必须先确认这一点。

只读，不写生产文件。
"""
import os
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

D_FIN = r'D:\astock\finance'
KEYS = ['ts_code', 'end_date', 'ann_date']
TABLES = ['income', 'cashflow', 'balancesheet', 'fina_indicator']


def _is_num(tp):
    """pyarrow.types 无 is_numeric_dtype，显式枚举数值类型。"""
    return (pa.types.is_integer(tp) or pa.types.is_floating(tp)
            or pa.types.is_decimal(tp))

for t in TABLES:
    p = os.path.join(D_FIN, t + '.parquet')
    pf = pq.ParquetFile(p)
    schema = pf.schema_arrow
    names = list(schema.names)
    md = pf.metadata
    size_mb = os.path.getsize(p) / 1024.0 / 1024.0
    print('=' * 92)
    print('%s  行数=%d  列数=%d  row_group=%d  大小=%.1f MB'
          % (t, md.num_rows, len(names), md.num_row_groups, size_mb))

    keys = [c for c in KEYS if c in names]
    df = pf.read(columns=keys).to_pandas()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()

    ed = pd.to_datetime(df['end_date'], format='mixed', errors='coerce')
    print('  end_date 范围 %s ~ %s' % (ed.min().date(), ed.max().date()))

    probe = pd.DataFrame({'ts_code': df['ts_code'].astype(str).values,
                          '_ed': ed.values})
    pos = pd.Series(range(len(probe)), index=pd.MultiIndex.from_arrays(
        [probe['ts_code'], probe['_ed']]))
    ordered = pos.sort_values(kind='stable').index.equals(pos.index)
    print('  物理行序已按 (ts_code,end_date) 升序: %s' % ordered)

    one = probe[probe['ts_code'] == probe['ts_code'].iloc[0]]
    print('  单只样本前12期: %s'
          % [str(x.date()) for x in one['_ed'].head(12)])

    skip = set(KEYS) | {'report_type'}
    num_cols = [c for c in names
                if c not in skip and not c.startswith('__index_level_')
                and _is_num(schema.field(c).type)]
    str_cols = [c for c in names
                if c not in skip and c not in num_cols
                and not c.startswith('__index_level_')]
    print('  数值候选列=%d  非数值列=%d %s'
          % (len(num_cols), len(str_cols), str_cols[:8]))
    print('  数值列名: %s' % num_cols[:40])
print('=' * 92)