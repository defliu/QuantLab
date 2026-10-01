# -*- coding: utf-8 -*-
"""XVAL 探索 4：_fill_f3_events 的 to_datetime 行为（生产同款调用）+ 000607 F3 复算。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import data_config as DC

print("=== FIN_FORECAST ann_date 原始格式分布 ===")
fc = pd.read_parquet(DC.FIN_FORECAST, columns=["ts_code", "ann_date", "p_change_max"])
print("dtype:", fc["ann_date"].dtype)
s = fc["ann_date"].astype(str)
pat = s.str.replace(r"\d", "N", regex=True)
print(pat.value_counts().head(10).to_string())

print("\n=== 生产同款 to_datetime(errors='coerce') ===")
parsed = pd.to_datetime(fc["ann_date"], errors="coerce")
print("NaT count:", parsed.isna().sum(), "/", len(fc))
# NaT 的原始格式分布
nat_pats = s[parsed.isna()].str.replace(r"\d", "N", regex=True)
print("NaT 格式分布:", nat_pats.value_counts().head(5).to_dict())
ok_pats = s[parsed.notna()].str.replace(r"\d", "N", regex=True)
print("OK 格式分布:", ok_pats.value_counts().head(5).to_dict())

fc["_d"] = parsed
T = pd.Timestamp("2026-09-28")
f1 = fc[(fc["ts_code"] == "000607.SZ") & (fc["_d"] <= T)].sort_values("_d")
print("\n000607.SZ forecast（可解析记录，最后 5 条）:")
print(f1[["ts_code", "ann_date", "_d", "p_change_max"]].tail(5).to_string())

print("\n=== FIN_EXPRESS ===")
ex = pd.read_parquet(DC.FIN_EXPRESS, columns=["ts_code", "ann_date"])
parsed_ex = pd.to_datetime(ex["ann_date"], errors="coerce")
ex["_d"] = parsed_ex
print("NaT count:", parsed_ex.isna().sum(), "/", len(ex))
e1 = ex[(ex["ts_code"] == "000607.SZ") & (ex["_d"] <= T)].sort_values("_d")
print("000607.SZ express 最后 3 条:", e1[["ann_date", "_d"]].tail(3).to_dict("records"))

print("\n=== FIN_DIVIDEND ===")
dv = pd.read_parquet(DC.FIN_DIVIDEND, columns=["ts_code", "ann_date", "cash_div_tax"])
parsed_dv = pd.to_datetime(dv["ann_date"], errors="coerce")
dv["_d"] = parsed_dv
print("NaT count:", parsed_dv.isna().sum(), "/", len(dv))
d1 = dv[(dv["ts_code"] == "000607.SZ") & (dv["_d"] <= T)].sort_values("_d")
print("000607.SZ dividend 最后 3 条:", d1[["ann_date", "_d", "cash_div_tax"]].tail(3).to_dict("records"))
