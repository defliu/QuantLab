# coding=utf-8
"""一键更新数据：astock parquet + CSV

数据源: E:/量化/行情数据更新池/
目标:
  1. E:/astock/daily/stock_daily.parquet (增量更新)
  2. D:/QMT_POOL/mfic_fin_data.csv (重新生成)

用法: python scripts/update_data.py
"""
import os
import pandas as pd
import numpy as np

ASTOCK_PATH = "E:/astock/daily/stock_daily.parquet"
CSV_PATH = "D:/QMT_POOL/mfic_fin_data.csv"
UPDATE_POOL = "E:/量化/行情数据更新池"


def normalize_dates(df):
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
    return df


def find_incremental_dirs():
    dirs = []
    for d in os.listdir(UPDATE_POOL):
        full_path = os.path.join(UPDATE_POOL, d)
        if os.path.isdir(full_path) and '增量' in d:
            daily_file = os.path.join(full_path, "stock_daily.parquet")
            if os.path.exists(daily_file):
                dirs.append((d, daily_file))
    return sorted(dirs)


def update_astock():
    """Step 1: 增量更新 astock parquet"""
    print("[1/2] Updating astock parquet...")

    existing = pd.read_parquet(ASTOCK_PATH)
    if isinstance(existing.index, pd.MultiIndex):
        existing = existing.reset_index()
    existing = normalize_dates(existing)
    old_max = existing['trade_date'].max()
    print("  Existing: %d rows, latest: %s" % (len(existing), old_max))

    inc_dirs = find_incremental_dirs()
    print("  Found %d incremental dirs" % len(inc_dirs))

    all_new = []
    for name, path in inc_dirs:
        try:
            inc = pd.read_parquet(path)
            if isinstance(inc.index, pd.MultiIndex):
                inc = inc.reset_index()
            inc = normalize_dates(inc)
            existing_dates = set(existing['trade_date'].unique())
            new_dates = set(inc['trade_date'].unique()) - existing_dates
            if new_dates:
                inc_new = inc[inc['trade_date'].isin(new_dates)]
                all_new.append(inc_new)
                print("  %s: %d new rows (%d new dates)" % (name, len(inc_new), len(new_dates)))
        except Exception as e:
            print("  %s: error %s" % (name, str(e)[:50]))

    if not all_new:
        print("  No new data")
        return False

    new_data = pd.concat(all_new, ignore_index=True)
    merged = pd.concat([existing, new_data], ignore_index=True)
    merged = merged.drop_duplicates(subset=['trade_date', 'ts_code'], keep='last')
    merged = merged.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)

    merged = merged.set_index(['trade_date', 'ts_code'])
    merged.to_parquet(ASTOCK_PATH, engine='pyarrow')

    new_max = merged.index.get_level_values('trade_date').max()
    print("  Updated: %d rows, latest: %s (+%s)" % (
        len(merged), new_max, (pd.Timestamp(new_max) - pd.Timestamp(old_max)).days))
    return True


def update_csv():
    """Step 2: 重新生成 CSV"""
    print("[2/2] Regenerating CSV...")

    df = pd.read_parquet(ASTOCK_PATH)
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()

    latest_date = df['trade_date'].max()
    latest = df[df['trade_date'] == latest_date].copy()
    print("  Date: %s, Stocks: %d" % (latest_date, len(latest)))

    pb = latest['pb']
    pe = latest['pe_ttm']
    roe = pd.Series(np.where((pe > 0) & (pb > 0), pb / pe * 100.0, np.nan), index=latest.index)

    out = pd.DataFrame({
        'ts_code': latest['ts_code'].values,
        'pb': latest['pb'].values,
        'pe_ttm': latest['pe_ttm'].values,
        'circ_mv': latest['circ_mv'].values,
        'amount': latest['amount'].values,
        'roe': roe.values,
    })
    out = out.dropna(subset=['pb', 'circ_mv'])

    out.to_csv(CSV_PATH, index=False, encoding='gbk')
    print("  CSV: %d stocks, %d in 0-30B range" % (
        len(out), ((out['circ_mv'] > 0) & (out['circ_mv'] < 300000)).sum()))
    print("  Saved to: %s" % CSV_PATH)


def main():
    print("=" * 50)
    print("QuantLab Data Update")
    print("=" * 50)
    print()

    updated = update_astock()
    print()
    update_csv()

    print()
    print("=" * 50)
    print("Update complete!")
    print("=" * 50)


if __name__ == "__main__":
    main()
