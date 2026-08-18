"""T0 数据预检：dividend.parquet字段、BP管线字段、大盘域circ_mv可得性"""
import pyarrow.parquet as pq
import pandas as pd
import numpy as np

# 5. circ_mv range on a recent date
df = pd.read_parquet('E:/astock/daily/stock_daily.parquet',
                      columns=['circ_mv','close','pb'])
df = df.reset_index()
recent = df[df['trade_date']=='2026-06-12']
n_stocks = len(recent)
n_nonnull = recent['circ_mv'].notna().sum()
top500_thr = recent['circ_mv'].nlargest(500).min()
top1000_thr = recent['circ_mv'].nlargest(1000).min()
bot30_n = int(n_stocks * 0.3)
bot30_max = recent['circ_mv'].nsmallest(bot30_n).max()

print('=== circ_mv on 2026-06-12 ===')
print(f'  Total stocks: {n_stocks}')
print(f'  Non-null circ_mv: {n_nonnull}')
print(f'  Top 500 threshold: {top500_thr:.0f} wan = {top500_thr/10000:.1f} yi')
print(f'  Top 1000 threshold: {top1000_thr:.0f} wan = {top1000_thr/10000:.1f} yi')
print(f'  Bottom 30% count: {bot30_n}')
print(f'  Bottom 30% circ_mv max: {bot30_max:.0f} wan = {bot30_max/10000:.1f} yi')

# 6. PB availability
pb_valid = recent['pb'].notna() & (recent['pb'] > 0)
bp_median = (1.0 / recent.loc[pb_valid, 'pb']).median()
print(f'\n=== pb on 2026-06-12 ===')
print(f'  Non-null pb: {recent["pb"].notna().sum()}')
print(f'  pb > 0: {pb_valid.sum()}')
print(f'  BP = 1/pb median: {bp_median:.4f}')

# 7. dividend.parquet ex_date/ann_date availability
div_df = pd.read_parquet('E:/astock/finance/dividend.parquet',
                          columns=['ts_code','end_date','ann_date','div_proc',
                                   'cash_div','cash_div_tax','ex_date','pay_date'])
print(f'\n=== dividend.parquet ===')
print(f'  Total rows: {len(div_df)}')
print(f'  div_proc values: {div_df["div_proc"].value_counts().to_dict()}')
print(f'  ex_date non-null: {div_df["ex_date"].notna().sum()}')
print(f'  ann_date non-null: {div_df["ann_date"].notna().sum()}')
impl = div_df[(div_df['div_proc']=='实施') & (div_df['cash_div_tax']>0) & (div_df['ex_date'].notna())]
print(f'  实施+cash_div_tax>0+ex_date: {len(impl)}')
sample = impl.head(5)
print(f'  Sample:')
for _, row in sample.iterrows():
    print(f'    {row["ts_code"]} end={row["end_date"]} ann={row["ann_date"]} ex={row["ex_date"]} cash_tax={row["cash_div_tax"]}')

# 8. fina_indicator ann_date + bps
fi_df = pd.read_parquet('E:/astock/finance/fina_indicator.parquet',
                         columns=['ts_code','end_date','ann_date','bps'])
print(f'\n=== fina_indicator BPS ===')
print(f'  Total rows: {len(fi_df)}')
print(f'  ann_date non-null: {fi_df["ann_date"].notna().sum()}')
print(f'  bps non-null: {fi_df["bps"].notna().sum()}')
print(f'  bps > 0: {(fi_df["bps"]>0).sum()}')

# 9. circ_mv PIT quarterly snapshot check
dates_sample = ['2019-01-02','2020-01-02','2021-01-04','2022-01-04','2023-01-03','2024-01-02','2025-01-02','2026-01-02']
print(f'\n=== circ_mv PIT snapshot check ===')
for d in dates_sample:
    sub = df[df['trade_date']==d]
    if len(sub) == 0:
        print(f'  {d}: NO DATA')
        continue
    n = sub['circ_mv'].notna().sum()
    if n >= 500:
        t500 = sub['circ_mv'].nlargest(500).min()
        print(f'  {d}: {n} stocks, Top500 thr={t500/10000:.1f} yi')
    else:
        print(f'  {d}: {n} stocks, <500 with circ_mv')

# 10. Check base_share field existence in dividend
div_full = pq.read_schema('E:/astock/finance/dividend.parquet')
has_base_share = 'base_share' in [f.name for f in div_full]
print(f'\n=== base_share in dividend.parquet: {has_base_share} ===')

# 11. Check cash_dv field
has_cash_dv = 'cash_dv' in [f.name for f in div_full]
print(f'=== cash_dv in dividend.parquet: {has_cash_dv} ===')

# 12. Verify we can compute dividend yield TTM from dividend.parquet
# Show yearly ex_date coverage
impl_sorted = impl.sort_values('ex_date')
impl_sorted['ex_year'] = impl_sorted['ex_date'].str[:4]
yearly_counts = impl_sorted.groupby('ex_year').size()
print(f'\n=== Dividend TTM feasibility: ex_date coverage by year ===')
for yr, cnt in yearly_counts.items():
    print(f'  {yr}: {cnt} records (实施+cash_div_tax>0+ex_date)')

print('\n=== T0 PRECHECK COMPLETE ===')
