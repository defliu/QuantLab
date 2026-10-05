# coding=utf-8
"""FULL vs MAIN 共有键数值一致性检查（只读，带 traceback）。"""
import os
import sys
import traceback

import pandas as pd
import pyarrow.parquet as pq

FULL = r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'
MAIN = r'D:\astock\daily\stock_daily.parquet'
KEYS = ['ts_code', 'trade_date']
CMP = ['close', 'vol', 'amount', 'pct_chg', 'turnover_rate', 'is_st',
       'adj_factor', 'circ_mv']


def load(path, tag):
    f = pq.ParquetFile(path)
    names = list(f.schema_arrow.names)
    use = [c for c in KEYS + CMP if c in names]
    t = f.read(columns=use).to_pandas()
    print('%-5s 读入列=%d  返回类型=%s  列名=%s'
          % (tag, len(use), type(t).__name__, list(t.columns)))
    if isinstance(t.index, pd.MultiIndex):
        print('     index 名称=%s' % list(t.index.names))
        t = t.reset_index()
        print('     reset 后列名=%s' % list(t.columns))
    print('     缺失键列=%s'
          % [k for k in KEYS if k not in t.columns])
    t['trade_date'] = pd.to_datetime(
        t['trade_date'], format='mixed', errors='coerce'
    )
    t = t.drop_duplicates(subset=KEYS, keep='first')
    print('     去重后=%d 行' % len(t))
    return t, use


def main():
    try:
        f2, use_f = load(FULL, 'FULL')
        d2, use_d = load(MAIN, 'MAIN')
        cmp_cols = [c for c in use_f if c in use_d and c not in KEYS]
        print('')
        print('参与比较的数值列: %s' % cmp_cols)
        mg = f2.merge(d2, on=KEYS, how='inner',
                      suffixes=('_f', '_d'))
        print('可比共有键: %d' % len(mg))
        print('')
        print('%-14s %10s %10s %12s %12s %12s'
              % ('列', '共同非空', '完全相同%', '中位绝对差',
                 '最大绝对差', '99.9%分位'))
        for c in cmp_cols:
            a = pd.to_numeric(mg[c + '_f'], errors='coerce')
            b = pd.to_numeric(mg[c + '_d'], errors='coerce')
            m = a.notna() & b.notna()
            n = int(m.sum())
            if n == 0:
                print('%-14s %10d %10s' % (c, 0, 'n/a'))
                continue
            av, bv = a[m], b[m]
            ad = (av - bv).abs()
            print('%-14s %10d %9.3f%% %12.6g %12.6g %12.6g'
                  % (c, n, float((ad == 0).mean() * 100),
                     float(ad.median()), float(ad.max()),
                     float(ad.quantile(0.999))))
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())