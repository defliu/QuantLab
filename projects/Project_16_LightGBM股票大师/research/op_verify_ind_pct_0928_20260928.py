# -*- coding: utf-8 -*-
"""主代理独立复算：核实 2026-09-28 实盘 ind_pct_ths 是真实普跌还是数据源异常。"""

import os

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
LIVE = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
THS = r"D:\astock\board\ths_daily.parquet"

live = pd.read_parquet(LIVE)
live["trade_date"] = pd.to_datetime(live["trade_date"])
d = live["trade_date"].max()

print("=== 快照 %s ind_pct_ths 全市场分布 ===" % d.date())
s = live["ind_pct_ths"].dropna()
print("n=%d  非空率=%.1f%%" % (len(s), 100.0 * live["ind_pct_ths"].notna().mean()))
print("p1=%.2f p10=%.2f p25=%.2f p50=%.2f p75=%.2f p90=%.2f p99=%.2f"
      % tuple(np.percentile(s, [1, 10, 25, 50, 75, 90, 99])))
print("min=%.2f max=%.2f mean=%.2f" % (s.min(), s.max(), s.mean()))
print("唯一值个数=%d（若很少说明来源异常）" % s.nunique())
print("最高频取值:")
print(s.value_counts().head(10).to_string())

print("\n=== 对照：ths_daily.parquet 源文件最大日期 ===")
td = pd.read_parquet(THS).reset_index()
if "trade_date" in td.columns and "level_0" in td.columns:
    td = td.drop(columns=["level_0"])
td["trade_date"] = pd.to_datetime(td["trade_date"])
n881 = td[td["ts_code"].astype(str).str.startswith("881")]
print("ths_daily 最大日期=%s" % td["trade_date"].max().date())
print("881 板块最后 8 个交易日:")
last8 = sorted(n881["trade_date"].unique())[-8:]
for dd in last8:
    v = n881[n881["trade_date"] == dd]["pct_change"].dropna()
    print("  %s  n=%3d  p25=%+.2f p50=%+.2f p75=%+.2f"
          % (pd.Timestamp(dd).date(), len(v),
             np.percentile(v, 25), np.percentile(v, 50), np.percentile(v, 75)))

print("\n=== 快照 ind_pct_ths 与 09-11 源文件是否同源同值 ===")
d11 = pd.Timestamp("2026-09-11")
lv = live[["ts_code", "ind_pct_ths"]].copy()
lv["ts_code"] = lv["ts_code"].astype(str)
src = n881[n881["trade_date"] == d11][
    ["ts_code", "pct_change"]].rename(columns={"pct_change": "src"})
mg = lv.merge(src, on="ts_code", how="inner")
print("09-11 可匹配股票数=%d" % len(mg))
if len(mg):
    diff = (mg["ind_pct_ths"] - mg["src"]).abs()
    print("与源文件差值: p50=%.4f p90=%.4f 完全相等占比=%.1f%%"
          % (diff.median(), diff.quantile(0.9), 100.0 * (diff < 1e-6).mean()))
    print("\n样本对照(前10):")
    print(mg.head(10).to_string(index=False))
