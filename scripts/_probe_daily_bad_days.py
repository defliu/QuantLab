# coding=utf-8
"""只读探针：判定 E 日线 is_st/listed_days 空值的性质。

要回答三件事：
 1. 那 3 个可疑日（2026-08-28 / 09-14 / 09-18）是整行不可信，还是仅个别列未填充？
 2. E 日线的真实起止日期，以及 5 天缺口两侧是否连续。
 3. 空值是否可通过"相邻交易日"重建。
"""
import sys

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as pads

E_FULL = r'E:\temp\行情数据\个股行情全量\stock_daily_full.parquet'
D_DAILY = r'D:\astock\daily\stock_daily.parquet'

SUSPECT = ['20260828', '20260914', '20260918']
NEIGHBOR = ['20260826', '20260827', '20260831', '20260901',
            '20260911', '20260915', '20260917', '20260921', '20260922']
GAP = ['20260626', '20260629', '20260630', '20260701', '20260702',
       '20260703', '20260706', '20260707']

COLS = ['open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount',
        'turnover_rate', 'pe', 'pe_ttm', 'pb', 'total_mv', 'circ_mv',
        'adj_factor', 'suspend_type', 'is_st', 'listed_days']


def dump(t):
    print('')
    print('=' * 84)
    print(t)
    print('=' * 84)


def to_flat(df):
    """D 主库带 pandas metadata：按列投影读取会把 trade_date/ts_code 还原成
    MultiIndex，不在 df.columns 里。条件式 reset_index 摊平。"""
    if 'trade_date' not in df.columns:
        df = df.reset_index()
    return df


def span(ds, label):
    t = to_flat(ds.to_table(columns=['trade_date']).to_pandas())
    t['trade_date'] = pd.to_datetime(t['trade_date'])
    print('  %-6s 行数=%-10d trade_date 范围=[%s .. %s] 交易日数=%d'
          % (label, len(t), t['trade_date'].min().date(),
             t['trade_date'].max().date(), t['trade_date'].nunique()))
    return t


def main():
    eset = pads.dataset(E_FULL, format='parquet')
    dset = pads.dataset(D_DAILY, format='parquet')

    dump('P1. E / D 日线起止日期')
    span(eset, 'E全量')
    span(dset, 'D主库')

    dump('P2. E 可疑日 vs 邻近正常日：逐列非空率（判定整行可疑 or 单列缺失）')
    want = pd.to_datetime(SUSPECT + NEIGHBOR + GAP)
    etype = eset.schema.field('trade_date').type
    cond = pads.field('trade_date').isin(pa.array(list(want), type=etype))
    df = eset.to_table(columns=['trade_date'] + COLS, filter=cond).to_pandas()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['d'] = df['trade_date'].dt.strftime('%Y%m%d')

    print('  --- 可疑日 / 邻近日 ---')
    print('  %-12s %7s  %s' % ('日期', '行数', '非空率<1.0 的列'))
    for d in SUSPECT + NEIGHBOR:
        sub = df[df['d'] == d]
        if len(sub) == 0:
            print('  %-12s %7d  <E 中该日不存在>' % (d, 0))
            continue
        rate = sub[COLS].notna().mean()
        bad = {c: round(v, 4) for c, v in rate.items() if v < 1.0}
        print('  %-12s %7d  %s' % (d, len(sub), bad if bad else '全部 18 列 100% 非空'))

    print('')
    print('  --- 5 天缺口两侧 ---')
    for d in GAP:
        sub = df[df['d'] == d]
        print('  E %-10s 行数=%d' % (d, len(sub)))

    dump('P3. is_st 相邻交易日取值分布（评估前向填充可行性）')
    for d in ['20260826', '20260827', '20260828', '20260831', '20260901',
              '20260911', '20260914', '20260915', '20260918', '20260921', '20260922']:
        sub = df[df['d'] == d]
        if len(sub) == 0:
            print('  %-12s <E 中该日不存在>' % d)
            continue
        vc = sub['is_st'].value_counts(dropna=False).to_dict()
        print('  %-12s 行数=%-6d is_st 分布=%s' % (d, len(sub), vc))

    dump('P4. D 中 is_st 是否可覆盖三个可疑日（D 末日=2026-09-11）')
    dtype = dset.schema.field('trade_date').type
    dcond = pads.field('trade_date').isin(
        pa.array(list(pd.to_datetime(SUSPECT + ['20260911'])), type=dtype)
    )
    sub = to_flat(dset.to_table(
        columns=['trade_date', 'ts_code', 'is_st', 'listed_days'],
        filter=dcond,
    ).to_pandas())
    sub['d'] = pd.to_datetime(sub['trade_date']).dt.strftime('%Y%m%d')
    for d in SUSPECT + ['20260911']:
        s = sub[sub['d'] == d]
        if len(s) == 0:
            print('  D %-10s <D 中该日不存在>' % d)
            continue
        print('  D %-10s 行数=%-6d is_st非空=%-6d listed_days非空=%d'
              % (d, len(s), int(s['is_st'].notna().sum()),
                 int(s['listed_days'].notna().sum())))

    print('')
    print('DAILY_BADDAYS_DIAG_DONE')


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
