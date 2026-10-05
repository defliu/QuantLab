# -*- coding: utf-8 -*-
"""_probe_daily_contract.py

目的：在实施「E 全量日线整体迁移」前，把写入契约一次性钉死，避免 to_parquet 时
破坏 D:/astock 的既有约定（index 结构 / 列 dtype / is_st 与 listed_days 覆盖）。

审计项：
  A. D / E 双边完整 schema + dtype 逐列对照
  B. index 结构与 index dtype
  C. is_st / listed_days 等关键列的非空覆盖（按双边日期分段）
  D. pyarrow 是否支持按 trade_date 过滤读取 D（决定能否避免把 1465 万行全量载入内存）
  E. 列级「E 空覆盖 D 非空」缺口统计（迁移后哪些列会掉值）

输出：_probe_daily_contract.out.txt
"""
import os
import sys

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

WATCH_COLS = ['is_st', 'listed_days', 'total_share', 'float_share', 'free_share',
              'circ_mv', 'total_mv', 'turnover_rate', 'turnover_rate_f',
              'pct_chg', 'change', 'close', 'pre_close', 'vol', 'amount']


def hr(title):
    print('')
    print('=' * 78)
    print(title)
    print('=' * 78)


def arrow_schema(path):
    return pq.read_schema(path)


def main():
    hr('A/B. 双边 schema / index / dtype 对照')
    ds, es = arrow_schema(D_DAILY), arrow_schema(E_FULL)

    print('D arrow schema: %d 列' % len(ds.names))
    print('E arrow schema: %d 列' % len(es.names))
    dnames, enames = list(ds.names), list(es.names)
    print('D 独有列: %s' % [c for c in dnames if c not in enames])
    print('E 独有列: %s' % [c for c in enames if c not in dnames])

    # pandas 侧实际 dtype（只读 schema 不够，pandas 会按 index 重建）
    # 只读第 0 个 row group 取样 dtype，避免把 1465 万行全量载入内存。
    print('')
    print('-- 读第 0 个 row group 取 pandas dtype（不整表载入）--')
    dhead = pq.ParquetFile(D_DAILY).read_row_group(0).to_pandas()
    ehead = pq.ParquetFile(E_FULL).read_row_group(0).to_pandas()
    print('D index.names = %s' % list(dhead.index.names))
    print('E index.names = %s' % list(ehead.index.names))
    print('D index dtypes = %s' % [str(dhead.index.get_level_values(n).dtype)
                                   for n in (dhead.index.names or [])])
    print('E index dtypes = %s' % [str(ehead.index.get_level_values(n).dtype)
                                   for n in (ehead.index.names or [])])
    print('')
    print('%-18s %-22s %-22s %s' % ('列', 'D dtype', 'E dtype', '一致'))
    allc = dnames + [c for c in enames if c not in dnames]
    for c in allc:
        dt = str(dhead[c].dtype) if c in dhead.columns else '(缺)'
        et = str(ehead[c].dtype) if c in ehead.columns else '(缺)'
        print('%-18s %-22s %-22s %s' % (c, dt, et, 'Y' if dt == et else '<<< DIFF'))

    hr('C. 关键列非空覆盖（D 全量 vs E 全量）')
    watch = [c for c in WATCH_COLS if c in dnames or c in enames]
    # 用 arrow 统计全量 null_count，避免 pandas 全量载入
    for label, path in (('D', D_DAILY), ('E', E_FULL)):
        sch = arrow_schema(path)
        pf = pq.ParquetFile(path)
        idx_col = None
        for cand in ('trade_date',):
            if cand in sch.names:
                idx_col = cand
        cols = [c for c in watch if c in sch.names]
        t = pf.read(columns=cols)
        print('')
        print('[%s] %s  行数=%d  列=%s' % (label, os.path.basename(path),
                                          pf.metadata.num_rows, cols))
        for c in cols:
            arr = t.column(c)
            nn = len(arr) - arr.null_count
            print('   %-16s 非空=%9d / %9d  (%.4f)  dtype=%s'
                  % (c, nn, len(arr), nn / max(len(arr), 1), arr.type))

    hr('C2. is_st / listed_days 在双边各自日期尾段的覆盖')
    for label, path in (('D', D_DAILY), ('E', E_FULL)):
        pf = pq.ParquetFile(path)
        cols = [c for c in ('is_st', 'listed_days') if c in arrow_schema(path).names]
        if not cols:
            continue
        t = pq.ParquetFile(path).read(columns=['trade_date'] + cols
                                      if 'trade_date' in arrow_schema(path).names else cols)
        td = t.column('trade_date').to_pandas()
        tail = sorted(set(td.astype(str)))[-8:]
        print('')
        print('[%s] 尾部日期段 %s' % (label, tail))
        sub = pd.DataFrame({c: t.column(c).to_pandas() for c in cols})
        sub['_d'] = td.astype(str).values
        for dt in tail:
            m = sub['_d'] == dt
            parts = []
            for c in cols:
                parts.append('%s 非空=%d/%d' % (c, int(sub.loc[m, c].notna().sum()), int(m.sum())))
            print('   %s  %s' % (dt, '  '.join(parts)))

    hr('D. pyarrow 按 trade_date 过滤读取 D（内存策略验证）')
    # 注意：PyArrow 25 已移除 ParquetFile.read(filters=...)；带过滤的读取走 pyarrow.dataset API。
    try:
        dset = pads.dataset(D_DAILY, format='parquet')
        tbl = dset.to_table(
            columns=['close'],
            filter=(pads.field('trade_date') >= '20260629')
                   & (pads.field('trade_date') <= '20260703'))
        print('过滤读取成功: 行数=%d（预期 D-only 五天合计 27562）' % tbl.num_rows)
        if tbl.num_rows == 27562:
            print('结论: 可用 dataset filter 只读缺口 5 天，无需把 1465 万行载入内存。')
        else:
            print('结论: 行数与预期 27562 不符，需核对 D-only 五天是否仍为 27,562 行。')
    except Exception as exc:
        print('过滤读取失败: %s: %s' % (type(exc).__name__, exc))
        print('结论: 退回全量载入或 row-group 级扫描。')

    hr('E. 列级「E 空覆盖 D 非空」缺口（迁移后会掉值的列）')
    # 只在共同日期段上比较才有意义；用 E 全量的非空总量做快速筛查
    print('（与 _probe_column_loss.out.txt 的财务表审计同口径；日线单独看）')
    dd = pq.ParquetFile(D_DAILY)
    ee = pq.ParquetFile(E_FULL)
    dsch, esch = arrow_schema(D_DAILY), arrow_schema(E_FULL)
    common = [c for c in dsch.names if c in esch.names]
    dt_ = dd.read(columns=common)
    et_ = ee.read(columns=common)
    print('%-18s %14s %14s %10s' % ('列', 'D非空', 'E非空', 'E/D'))
    risk = []
    for c in common:
        da = dt_.column(c)
        ea = et_.column(c)
        dnn = len(da) - da.null_count
        enn = len(ea) - ea.null_count
        ratio = (enn / dnn) if dnn else 1.0
        flag = '  <<< 显著掉值' if dnn > 1000 and ratio < 0.5 else ''
        if flag:
            risk.append((c, dnn, enn, ratio))
        print('%-18s %14d %14d %10.4f%s' % (c, dnn, enn, ratio, flag))
    print('')
    print('显著掉值列数 = %d' % len(risk))
    for c, dnn, enn, ratio in risk:
        print('   %-18s D非空=%d E非空=%d 保留率=%.4f' % (c, dnn, enn, ratio))

    print('')
    print('DAILY_CONTRACT_DONE')


if __name__ == '__main__':
    main()