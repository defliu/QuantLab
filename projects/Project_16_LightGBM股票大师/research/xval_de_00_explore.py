# -*- coding: utf-8 -*-
"""XVAL 探索：快照/资金流/daily_basic/回测面板的列与日期范围（只读）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import data_config as DC

PROJ = DC.PROJECT_DIR

print("=== g2_live() ===")
m, meta = DC.g2_live()
print("model:", m)
print("meta :", meta)
print("exists:", os.path.exists(m), os.path.exists(meta))

print("\n=== snapshot g2_latest_features.parquet ===")
snap = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
print("rows:", len(snap), "dates:", sorted(pd.to_datetime(snap["trade_date"]).dt.date.unique()))
print("cols:", list(snap.columns))

print("\n=== moneyflow.parquet freshness ===")
mf = pd.read_parquet("D:/astock/moneyflow/moneyflow.parquet", columns=["net_mf_amount"])
print("index names:", mf.index.names)
latest = pd.Timestamp(mf.index.get_level_values("trade_date").max())
print("latest:", latest.date(), "| rows:", len(mf))

print("\n=== DAILY_BASIC ===")
print("exists:", os.path.exists(DC.DAILY_BASIC))
if os.path.exists(DC.DAILY_BASIC):
    db = pd.read_parquet(DC.DAILY_BASIC, columns=["pe_ttm", "turnover_rate"]).reset_index()
    print("cols:", list(db.columns))
    db["trade_date"] = pd.to_datetime(db["trade_date"])
    print("rows:", len(db), "max date:", db["trade_date"].max().date())
    print("hit 2026-09-28:", (db["trade_date"] == "2026-09-28").sum())
    print("hit 2026-09-25:", (db["trade_date"] == "2026-09-25").sum())

print("\n=== incremental_daily.parquet ===")
incr = pd.read_parquet(os.path.join(DC.LIVE_DIR, "incremental_daily.parquet"))
incr["trade_date"] = pd.to_datetime(incr["trade_date"])
print("rows:", len(incr), "max:", incr["trade_date"].max().date(),
      "dates:", sorted(incr["trade_date"].dt.date.unique())[-5:])

print("\n=== bt panel (columns only, lazy) ===")
import pyarrow.parquet as pq
pf = pq.ParquetFile(os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"))
print("num_rows:", pf.metadata.num_rows)
cols = pf.schema_arrow.names
print("cols(%d):" % len(cols), cols)
