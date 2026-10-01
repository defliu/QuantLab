# -*- coding: utf-8 -*-
"""XVAL 探索 5：suspend_timing / up_limit 值域、面板 dtype。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import data_config as DC

d = pd.read_parquet(DC.MAIN_DAILY, columns=["suspend_timing", "up_limit", "down_limit", "open", "vol"])
print("suspend_timing dtype:", d["suspend_timing"].dtype)
print("suspend_timing value_counts:", d["suspend_timing"].value_counts(dropna=False).head(5).to_dict())
print("up_limit dtype:", d["up_limit"].dtype, "sample:", d["up_limit"].dropna().head(3).tolist())
print("open dtype:", d["open"].dtype)

import pyarrow.parquet as pq
pf = pq.ParquetFile(os.path.join(DC.DATA_DIR, "feature_panel_v3_enh2_n3_bt.parquet"))
t = pf.schema_arrow
for c in ["ts_code", "trade_date", "main_net", "industry_pct", "fwd_ret", "mf_main_net"]:
    print(c, "->", t.field(c).type)
