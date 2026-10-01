# -*- coding: utf-8 -*-
"""XVAL 探索 3：000607 估值/F3 事件源 / 面板 main_net 量纲 / daily_basic 命中。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import data_config as DC

print("=== daily_basic 2026-09-28 est（deploy 口径） ===")
daily = pd.read_parquet(DC.DAILY_BASIC, columns=["pe_ttm", "turnover_rate"]).reset_index()
daily["trade_date"] = pd.to_datetime(daily["trade_date"])
daily["ts_code"] = daily["ts_code"].astype(str)
print("rows:", len(daily), "max:", daily["trade_date"].max().date())
d28 = daily[daily["trade_date"] == "2026-09-28"]
print("0928 rows:", len(d28), "pe_ttm notna:", d28["pe_ttm"].notna().sum())
r = d28[d28["ts_code"] == "000607.SZ"]
print("000607.SZ 0928:", r.to_dict("records"))

snap = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
pre = snap  # 全池 5839，看整体命中率
idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
daily = daily.set_index(["trade_date", "ts_code"])
est = daily.reindex(idx)
print("全池 est pe_ttm 命中率:", est["pe_ttm"].notna().mean())

print("\n=== 000607.SZ F3 事件源（PIT <= 2026-09-28） ===")
fc = pd.read_parquet(DC.FIN_FORECAST, columns=["ts_code", "ann_date", "p_change_max"])
ex = pd.read_parquet(DC.FIN_EXPRESS, columns=["ts_code", "ann_date"])
dv = pd.read_parquet(DC.FIN_DIVIDEND, columns=["ts_code", "ann_date", "cash_div_tax"])
fc["ann_date"] = pd.to_datetime(fc["ann_date"])
ex["ann_date"] = pd.to_datetime(ex["ann_date"])
dv["ann_date"] = pd.to_datetime(dv["ann_date"])
T = pd.Timestamp("2026-09-28")
f1 = fc[(fc["ts_code"] == "000607.SZ") & (fc["ann_date"] <= T)].sort_values("ann_date")
print("forecast last 3:", f1.tail(3).to_dict("records"))
e1 = ex[(ex["ts_code"] == "000607.SZ") & (ex["ann_date"] <= T)].sort_values("ann_date")
print("express last 3:", e1.tail(3).to_dict("records"))
d1 = dv[(dv["ts_code"] == "000607.SZ") & (dv["ann_date"] <= T)].sort_values("ann_date")
print("dividend last 3:", d1.tail(3).to_dict("records"))

print("\n=== 面板 main_net / industry_pct 量纲（回测引擎用） ===")
pan = pd.read_parquet(os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"),
                      columns=["trade_date", "ts_code", "main_net", "industry_pct",
                               "ind_pct_ths", "mf_main_net", "fwd_ret", "pe_ttm", "turn_ma5"])
pan = pan[pan["trade_date"] >= "2024-07-01"]
print("rows:", len(pan))
print(pan[["main_net", "industry_pct", "ind_pct_ths", "mf_main_net", "fwd_ret"]].describe(
    percentiles=[0.01, 0.5, 0.99]).to_string())
same = (pan["main_net"] - pan["mf_main_net"] * 1e4).abs()
print("main_net ≈ mf_main_net*1e4 ?  中位差:", same.median(), " 均值差:", same.mean())
samei = (pan["industry_pct"] - pan["ind_pct_ths"]).abs()
print("industry_pct ≈ ind_pct_ths ?  最大差:", samei.max())
