# -*- coding: utf-8 -*-
"""XVAL 探索 2：面板日期范围 / meta 特征 / MAIN_DAILY 布局 / 快照 F2 零值统计。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import pyarrow.parquet as pq

import data_config as DC

print("=== bt panel date range ===")
pf = pq.ParquetFile(os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"))
# 只读 trade_date 列分块统计 min/max
tbl = pq.read_table(os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"),
                    columns=["trade_date"])
td = tbl.to_pandas()
td = pd.to_datetime(td["trade_date"])
print("rows:", len(td), "min:", td.min().date(), "max:", td.max().date())
print("dates in [2024-07-01, 2026-08-14]:", ((td >= "2024-07-01") & (td <= "2026-08-14")).sum())
del tbl, td

print("\n=== meta feature_cols (0825 vs 0915) ===")
for p in [os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260825.json"),
          os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260915.json")]:
    m = json.load(open(p, encoding="utf-8"))
    print(p, "->", len(m["feature_cols"]), "features")
    print("  ", m["feature_cols"])

print("\n=== MAIN_DAILY layout ===")
d = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "up_limit", "down_limit", "vol",
                                            "suspend_timing", "pe_ttm", "turnover_rate"])
print("index names:", d.index.names, "| cols:", list(d.columns))
if d.index.names[0] is not None:
    print("max trade_date:", pd.Timestamp(d.index.get_level_values("trade_date").max()).date())
else:
    print("flat layout, head:")
    print(d.head(2))

print("\n=== snapshot top100 F2 零值统计 ===")
snap = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
meta = json.load(open(os.path.join(DC.DATA_DIR, "features_v3_g2_strong_real_20260915.json"),
                      encoding="utf-8"))
import lightgbm as lgb
bst = lgb.Booster(model_file="D:/QuantLab/models/lgb_model_v3_g2_strong_real_20260915_1522t.txt")
snap["prob"] = bst.predict(snap[meta["feature_cols"]].astype("float32").values)
pre = snap.nlargest(100, "prob")
print("top100 prob range:", pre["prob"].min(), "-", pre["prob"].max())
mn = pre["mf_main_net"]
print("top100 mf_main_net: n_zero=%d n_nan=%d n_nonzero=%d" % ((mn == 0).sum(), mn.isna().sum(), (mn != 0).sum()))
print("000607.SZ in top100:", (pre["ts_code"] == "000607.SZ").any())
row = pre[pre["ts_code"] == "000607.SZ"]
if len(row):
    r = row.iloc[0]
    print("  prob=%.6f mf_main_net=%r ind_pct_ths=%r pe_ttm=%r turn_ma5=%r volume_ratio=%r"
          % (r["prob"], r["mf_main_net"], r["ind_pct_ths"], r["pe_ttm"], r["turn_ma5"], r["volume_ratio"]))
    print("  fc_days_since=%r ex_days_since=%r fc_pchange=%r dv_year_sum=%r"
          % (r["fc_days_since"], r["ex_days_since"], r["fc_pchange"], r["dv_year_sum"]))
print("ind_pct_ths stats (top100):", pre["ind_pct_ths"].describe().to_dict())
