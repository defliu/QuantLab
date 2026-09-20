# coding: utf-8
"""对比 DE 缓存的 pools pkl 与 audit_r8 的 per_day 选股是否一致（V14 口径）。

用途：定位 audit_r8 复算（+28.2%）与 DE 报告（+6.9%）V14 K2 的 21pp 差异来源。
若选股一致 → 差异来自出场规则（fixed STOP/TP vs live_trail TRAIL）。
"""
import json
import os
import pickle
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, r"D:\QuantLab\projects\Project_16_LightGBM股票大师")
sys.path.insert(0, r"D:\QuantLab\data\schedules")
import data_config as DC
import audit_r8 as A

START, END = "2024-07-01", "2026-08-14"
PKL = r"D:\QuantLab\data\schedules\de_r8_scripts\out\pools_V14_main.pkl"


def load_de():
    with open(PKL, "rb") as f:
        d = pickle.load(f)
    return d["dates"], d["per_day"]


def build_mine():
    panel = pd.read_parquet(os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet"))
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    meta = json.load(open(os.path.join(DC.DATA_DIR, "features_v3.json"), encoding="utf-8"))
    booster = lgb.Booster(model_file=DC.model_file("_v3"))
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "high", "low", "close", "up_limit",
                                                    "down_limit", "vol", "suspend_timing"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["trade_date"] >= pd.Timestamp(START) - pd.Timedelta(days=45)]
    daily = daily.sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1).set_index(["trade_date", "ts_code"])
    dates, per_day = A.build_signal(panel, meta, booster, daily, 58.0, START, END)
    return dates, per_day


def main():
    de_dates, de_per = load_de()
    my_dates, my_per = build_mine()
    print("DE dates:", len(de_dates), "mine dates:", len(my_dates))
    de_set = set(de_dates)
    my_set = set(my_dates)
    print("date overlap:", len(de_set & my_set), "only_de:", len(de_set - my_set), "only_mine:", len(my_set - de_set))

    n_common5 = n_common10 = diff5 = 0
    n_total_match = 0
    total_checked = 0
    same_cand_set = 0
    cand_diff = 0
    for d in sorted(de_set & my_set):
        de_row = de_per[d]      # index=ts_code, sorted by total desc
        my_row = my_per[d]      # index=ts_code, sorted by sc_total desc
        de_codes = list(de_row.index)
        my_codes = list(my_row.index)
        # 候选集合（top10 内）是否一致
        if set(de_codes) == set(my_codes):
            same_cand_set += 1
        else:
            cand_diff += 1
            if cand_diff <= 3:
                print("CAND_DIFF", d.date(),
                      "DE:", de_codes[:3], "ME:", my_codes[:3])
        # 排序前5/前10是否一致
        if de_codes[:5] == my_codes[:5]:
            n_common5 += 1
        else:
            diff5 += 1
            if diff5 <= 3:
                print("ORDER_DIFF", d.date(), "DE:", de_codes[:3], "ME:", my_codes[:3])
        if de_codes[:10] == my_codes[:10]:
            n_common10 += 1
        # 单票 total 值对比（取 DE 第一票）
        if len(de_codes) and len(my_codes):
            c0 = de_codes[0]
            if c0 in my_row.index:
                de_t = de_row.loc[c0, "total"]
                my_t = my_row.loc[c0, "sc_total"]
                total_checked += 1
                if abs(de_t - my_t) < 1e-6:
                    n_total_match += 1
    n_all = len(de_set & my_set)
    print("candidate set same:", same_cand_set, "/", n_all, " diff:", cand_diff)
    print("top5 order same:", n_common5, "/", n_all, " diff:", diff5)
    print("top10 order same:", n_common10, "/", n_all)
    print("total value match(DE top1):", n_total_match, "/", total_checked)


if __name__ == "__main__":
    main()
