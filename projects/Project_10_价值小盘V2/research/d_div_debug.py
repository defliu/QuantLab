# coding=utf-8
import sys, os
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import run_grid_validation as rgv
import pandas as pd, numpy as np

div = pd.read_parquet(r"E:/astock/finance/dividend.parquet")
div = div[div["div_proc"].astype(str).str.strip() == "实施"].copy()
div = div[div["ex_date"].notna()].copy()
div["ex_date"] = pd.to_datetime(div["ex_date"])
div = div[div["cash_div_tax"].notna() & (div["cash_div_tax"] > 0)].copy()
dw = div.pivot_table(index="ex_date", columns="ts_code", values="cash_div_tax", aggfunc="sum").sort_index().fillna(0.0)
cum = dw.cumsum()
all_dates = pd.DatetimeIndex(sorted(rgv.panel.index.get_level_values("trade_date").unique()))
print("all_dates dtype", all_dates.dtype, "range", all_dates.min(), all_dates.max(), "n", len(all_dates))
print("cum index dtype", cum.index.dtype)
cum_now = cum.reindex(all_dates).ffill().fillna(0.0)
cum_365 = cum.reindex(all_dates - pd.Timedelta(days=365)).ffill().fillna(0.0)
ttm = cum_now - cum_365
print("ttm 2024-01-02 nonzero", int((ttm.loc[pd.Timestamp("2024-01-02")] > 0).sum()))
close_wide = rgv.panel["close"].unstack("ts_code").reindex(all_dates)
print("close 2024-01-02 nonnan", int(close_wide.loc[pd.Timestamp("2024-01-02")].notna().sum()))
print("close 2024-01-02 nonzero", int((close_wide.loc[pd.Timestamp("2024-01-02")] > 0).sum()))
dy = ttm / close_wide.replace(0, np.nan)
print("dy 2024-01-02 nonnan", int(dy.loc[pd.Timestamp("2024-01-02")].notna().sum()))
print("dy 2024-01-02 >0", int((dy.loc[pd.Timestamp("2024-01-02")] > 0).sum()))
s = dy.loc[pd.Timestamp("2024-01-02")].dropna()
print("dy sample top8:\n", s.sort_values(ascending=False).head(8).to_string())