# -*- coding: utf-8 -*-
"""_probe_daily_writecontract.py

目的：钉死「E 全量日线 -> D 主库」写入契约的三个未决问题（在写任何文件前必须定案）：

  Q1. E 用 pd.read_parquet 读出来的**精确列清单**、是否残留 'index' 列、
      物理列数 vs pandas 列数的关系（决定 to_parquet 前要不要 set_index / drop）。
  Q2. E 的 is_st / listed_days 空值**分布到哪些日期**（E 明显比 D 掉值：
      D is_st 非空 100%，E 非空 99.89%）。量化「全量替换会丢多少 is_st」。
  Q3. 修正 PyArrow 过滤读取的类型错误（D 的 trade_date 是 timestamp[ns]，
      之前拿字符串比较 -> greater_equal 无 kernel）。

输出：_probe_daily_writecontract.out.txt
"""
import os
import sys
from collections import Counter

import pandas as pd
import pyarrow.dataset as pads
import pyarrow.parquet as pq

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

D_DAILY = r'D:\astock\daily\stock_daily.parquet'
E_FULL = r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'

GAP_DATES = ['20260629', '20260630', '20260701', '20260702', '20260703']


def hr(title):
    print('')
    print('=' * 78)
    print(title)
    print('=' * 78)


def to_flat(df, key='trade_date'):
    """D 主库带 pandas metadata：过滤后 to_pandas() 会把 trade_date/ts_code 还原成
    MultiIndex，后续按列访问会 KeyError。条件式 reset_index 摊平。"""
    if key not in df.columns:
        df = df.reset_index()
    return df


def main():
    hr('Q1. 双边 pandas 列清单 / 物理列 / index 结构')
    dsch = pq.read_schema(D_DAILY)
    esch = pq.read_schema(E_FULL)
    print('D 物理列名(%d): %s' % (len(dsch.names), list(dsch.names)))
    print('E 物理列名(%d): %s' % (len(esch.names), list(esch.names)))
    print('D 独有物理列: %s' % [c for c in dsch.names if c not in esch.names])
    print('E 独有物理列: %s' % [c for c in esch.names if c not in dsch.names])

    d0 = pq.ParquetFile(D_DAILY).read_row_group(0).to_pandas()
    e0 = pq.ParquetFile(E_FULL).read_row_group(0).to_pandas()
    print('')
    print('D rowgroup0 -> index.names=%s  n_columns=%d' % (list(d0.index.names), len(d0.columns)))
    print('D columns(%d): %s' % (len(d0.columns), list(d0.columns)))
    print('E rowgroup0 -> index.names=%s  n_columns=%d' % (list(e0.index.names), len(e0.columns)))
    print('E columns(%d): %s' % (len(e0.columns), list(e0.columns)))

    # 关键：D 的 index 层 = E 的数据列？
    print('')
    print('D index 层名: %s' % list(d0.index.names))
    print('E 是否含同名列: %s' % {n: (n in e0.columns) for n in (d0.index.names or [])})
    print('E 是否含残留 index 列: %s' % ('index' in e0.columns))
    if 'index' in e0.columns:
        print('   E["index"] dtype=%s head=%s' % (e0['index'].dtype, list(e0['index'].head(5))))

    hr('Q2. E 的 is_st / listed_days 空值按日期分布（对照 D）')
    for label, path in (('D', D_DAILY), ('E', E_FULL)):
        pf = pq.ParquetFile(path)
        sch = pq.read_schema(path)
        cols = [c for c in ('is_st', 'listed_days', 'trade_date') if c in sch.names]
        t = pf.read(columns=cols)
        td = t.column('trade_date').to_pandas()
        is_st = t.column('is_st').to_pandas() if 'is_st' in cols else None
        ld = t.column('listed_days').to_pandas() if 'listed_days' in cols else None
        print('')
        print('[%s] 行数=%d' % (label, len(td)))
        for cname, ser in (('is_st', is_st), ('listed_days', ld)):
            if ser is None:
                continue
            nul = ser.isna()
            print('  %-12s 总空=%d (%.4f%%)' % (cname, int(nul.sum()), 100.0 * nul.sum() / len(ser)))
            if nul.sum():
                bydate = Counter(td[nul].astype(str))
                print('     空值日期段(%d 个): %s' % (len(bydate), sorted(bydate)[:15]))
                top = bydate.most_common(8)
                print('     空值最多的日期: %s' % top)

    hr('Q3. 修正后的 D 缺口五天过滤读取（timestamp 比较）')
    try:
        dset = pads.dataset(D_DAILY, format='parquet')
        lo = pd.Timestamp(GAP_DATES[0])
        hi = pd.Timestamp(GAP_DATES[-1])
        tbl = dset.to_table(columns=['trade_date', 'ts_code', 'close'],
                            filter=(pads.field('trade_date') >= lo)
                                   & (pads.field('trade_date') <= hi))
        print('过滤读取成功: 行数=%d（预期 D-only 五天合计 27562）' % tbl.num_rows)
        sub = to_flat(tbl.to_pandas())
        g = sub.groupby(sub['trade_date'].dt.strftime('%Y%m%d')).size()
        print('按日分布: %s' % dict(g))
        print('ts_code 去重数=%d（应≈当日全市场股票数）' % int(sub['ts_code'].nunique()))
        print('结论: dataset filter 可用 -> 缺口五天可低成本单独取出，供「补洞」方案使用。')
    except Exception as exc:
        print('过滤读取失败: %s: %s' % (type(exc).__name__, exc))

    hr('Q4. D 缺口五天是否在 E 全量中真的完全不存在（复核）')
    try:
        eset = pads.dataset(E_FULL, format='parquet')
        etbl = eset.to_table(columns=['trade_date', 'ts_code'],
                             filter=(pads.field('trade_date') >= pd.Timestamp(GAP_DATES[0]))
                                    & (pads.field('trade_date') <= pd.Timestamp(GAP_DATES[-1])))
        print('E 在缺口五天区间行数=%d（若为 0 则确认 E 缺这五天，需从 D 补）' % etbl.num_rows)
        if etbl.num_rows > 0:
            g = to_flat(etbl.to_pandas())
            print('  E 实际覆盖日期: %s' % sorted(set(g["trade_date"].dt.strftime("%Y%m%d"))))
    except Exception as exc:
        print('E 缺口区间读取失败: %s: %s' % (type(exc).__name__, exc))

    print('')
    print('WRITECONTRACT_DONE')


if __name__ == '__main__':
    main()
