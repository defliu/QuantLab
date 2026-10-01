# -*- coding: utf-8 -*-
"""T-20260928-G2DRY 任务1 步骤1（只读）：检查 ths_daily 与回测面板 ind_pct_ths 的原始语义。

输出到 stdout，不写任何生产数据。
"""
import os
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ASTOCK = "D:/astock"
PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
PANEL = os.path.join(PROJ, "data", "feature_panel_v3_enh2_n3_bt.parquet")

print("=" * 80)
print("[A] ths_daily.parquet 元信息")
td_file = os.path.join(ASTOCK, "board", "ths_daily.parquet")
pf = pq.ParquetFile(td_file)
print("schema:", pf.schema_arrow.names)
print("rows:", pf.metadata.num_rows)
td = pd.read_parquet(td_file).reset_index()
print("index cols after reset:", [c for c in td.columns[:3]])
print(td.head(3).to_string())
td["trade_date"] = pd.to_datetime(td["trade_date"])
td["ts_code"] = td["ts_code"].astype(str)
print("date range:", td["trade_date"].min().date(), "~", td["trade_date"].max().date())
print("ts_code samples:", td["ts_code"].unique()[:5])
n881 = td[td["ts_code"].str.startswith("881")]
print("881 rows:", f"{len(n881):,}", "| unique 881 codes:", n881["ts_code"].nunique())
dup = n881.duplicated(subset=["ts_code", "trade_date"]).sum()
print("881 (ts_code,trade_date) 重复行数:", dup)
print("\n881 pct_change 全期分位:")
print(n881["pct_change"].describe(percentiles=[.01, .05, .25, .5, .75, .95, .99]).to_string())
print("\n881 pct_change 按年均值/中位:")
n881y = n881.copy()
n881y["year"] = n881y["trade_date"].dt.year
print(n881y.groupby("year")["pct_change"].agg(["count", "mean", "median", "max", "min"]).to_string())
print("\n881 close/pct_change 单板块抽查 (881101, 最近30行):")
sub = n881[n881["ts_code"].str.startswith("881101")].sort_values("trade_date").tail(30)
print(sub[["trade_date", "close", "pct_change"]].to_string())
# 验证 pct_change 是否=当日涨跌幅：close/pre_close-1
if "pre_close" in n881.columns:
    chk = n881.dropna(subset=["close", "pre_close"]).copy()
    chk["calc"] = (chk["close"] / chk["pre_close"] - 1) * 100
    chk["diff"] = (chk["calc"] - chk["pct_change"]).abs()
    print("\npct_change vs close/pre_close-1 (%): mean|diff| =", chk["diff"].mean(),
          "| >0.1 占比:", (chk["diff"] > 0.1).mean())
else:
    print("\n(无 pre_close 列)")
# 按日 unique 板块数（检查 08-12 vs 08-14 覆盖）
for d in ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]:
    dd = n881[n881["trade_date"] == d]
    print(f"ths_daily 881 @ {d}: rows={len(dd)} pct median={dd['pct_change'].median() if len(dd) else float('nan')}")

print("\n" + "=" * 80)
print("[B] 回测面板 ind_pct_ths 概况")
ppf = pq.ParquetFile(PANEL)
cols = ppf.schema_arrow.names
print("panel rows:", ppf.metadata.num_rows)
print("panel cols:", cols)
ind_cols = [c for c in ["trade_date", "ts_code", "ths_ind", "ind_pct_ths", "ind_net_ths",
                        "industry_pct", "ind_pct_proxy", "fwd_ret", "fwd_ret3", "label3"] if c in cols]
pan = pd.read_parquet(PANEL, columns=ind_cols)
pan["trade_date"] = pd.to_datetime(pan["trade_date"])
print("panel date range:", pan["trade_date"].min().date(), "~", pan["trade_date"].max().date())
print("\nind_pct_ths 按年统计（面板行级）:")
py = pan.copy()
py["year"] = py["trade_date"].dt.year
print(py.groupby("year")["ind_pct_ths"].agg(
    count="size", nonnull=lambda s: s.notna().sum(),
    nonnull_rate=lambda s: s.notna().mean(),
    p5=lambda s: s.quantile(.05), p50=lambda s: s.quantile(.5), p95=lambda s: s.quantile(.95),
    max="max", min="min").to_string())
print("\n面板尾部 8 个交易日逐日统计:")
last_dates = sorted(pan["trade_date"].unique())[-8:]
for d in last_dates:
    dd = pan[pan["trade_date"] == d]
    s = dd["ind_pct_ths"]
    print(f"{pd.Timestamp(d).date()}: n={len(dd):,} nonnull={s.notna().mean():.3f} "
          f"p5={s.quantile(.05):.3f} p50={s.median():.3f} p95={s.quantile(.95):.3f} max={s.max():.3f}")
print("\nths_ind 缺失率:", pan["ths_ind"].isna().mean() if "ths_ind" in pan.columns else "N/A")
if "industry_pct" in pan.columns:
    same = (pan["industry_pct"] == pan["ind_pct_ths"]) | (pan["industry_pct"].isna() & pan["ind_pct_ths"].isna())
    print("industry_pct == ind_pct_ths 占比:", same.mean())
