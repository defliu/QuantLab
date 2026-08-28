# coding: utf-8
"""周更面板刷新：合并主库+增量 → 重建 v2 → 切片 v3（供 deploy_predict / train_optuna 使用）。

背景：2026-08-28 发现 feature_panel_v3.parquet 停在 8/14 —— 周更 retrain 只跑 build_features_v2
（写 v2 面板），而 v3 面板要靠 WORKFLOW_DEPLOY.md 的一行手工命令切片，不在任何自动化里。
本脚本把「合并增量 + 重建 v2 + 切片 v3」固化，供 run_scheduled.ps1 的 retrain 模式调用。

用法：
  python refresh_panel_v3.py
注意：
  - 主数据 E:/astock 只读，绝不修改；合并日线写 data_live/merged_daily_full.parquet（临时）
  - 增量只保留到最后完整收盘日（当天盘中不完整则排除）
  - 慢变量（换手/估值/is_st/adj_factor）从主库每股最后一行前向填充（与 merge_live_features 同口径）
"""
import datetime
import json
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402

MAIN = DC.MAIN_DAILY
INCR = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")
MERGED = os.path.join(DC.LIVE_DIR, "merged_daily_full.parquet")
DATA = DC.DATA_DIR
SLOW = ["turnover_rate", "volume_ratio", "pe_ttm", "pb", "dv_ttm", "circ_mv",
        "is_st", "adj_factor", "total_share", "float_share", "free_share",
        "total_mv", "pe", "ps", "ps_ttm", "dv_ratio"]


def build_merged():
    print("[refresh][1/3] 合并主库 + 增量日线 ...")
    main = pd.read_parquet(MAIN).reset_index()
    main["trade_date"] = pd.to_datetime(main["trade_date"])
    main["ts_code"] = main["ts_code"].astype(str)
    main_last = main.sort_values(["ts_code", "trade_date"]).groupby("ts_code").tail(1).set_index("ts_code")
    if not os.path.exists(INCR):
        print("  !! 无增量库，仅用主库")
        return None
    incr = pd.read_parquet(INCR).reset_index()
    incr["trade_date"] = pd.to_datetime(incr["trade_date"])
    incr["ts_code"] = incr["ts_code"].astype(str)
    today = pd.Timestamp(datetime.date.today())
    incr = incr[incr["trade_date"] < today].copy()  # 剔除当天盘中不完整行
    if len(incr) == 0:
        print("  !! 增量无完整收盘日，仅用主库")
        return None
    incr = incr.rename(columns={"volume": "vol"})
    incr["close"] = incr["close"].astype(float)
    incr["vol"] = incr["vol"].astype(float)
    incr["amount"] = incr["amount"].astype(float)
    pre = incr["preClose"].astype(float).replace(0, np.nan)
    incr["pct_chg"] = (incr["close"] / pre - 1.0) * 100.0
    for c in SLOW:
        if c in main_last.columns:
            incr[c] = incr["ts_code"].map(main_last[c])
    for c in main.columns:
        if c not in incr.columns:
            incr[c] = incr["ts_code"].map(main_last[c]) if c in main_last.columns else np.nan
    merged = pd.concat([main, incr[main.columns]], ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    merged = merged.sort_values(["ts_code", "trade_date"]).set_index(["ts_code", "trade_date"])
    merged.to_parquet(MERGED)
    print(f"  合并日线 {len(merged):,} 行 | 最新 {merged.index.get_level_values('trade_date').max().date()}")
    return MERGED


def main():
    merged = build_merged()
    if merged:
        DC.MAIN_DAILY = merged
    print("[refresh][2/3] 重建 v2 面板 ...")
    import build_features_v2
    build_features_v2.main()
    print("[refresh][3/3] 切片生成 v3 面板 ...")
    df = pd.read_parquet(os.path.join(DATA, "feature_panel_v2.parquet"))
    m = json.load(open(os.path.join(DATA, "features_v3.json"), encoding="utf-8"))
    cols = [c for c in m["feature_cols"] if c in df.columns]
    df[cols + ["trade_date", "ts_code", "label", "fwd_ret"]].to_parquet(
        os.path.join(DATA, "feature_panel_v3.parquet"))
    print(f"  v3 面板已切片 | 最新日 {pd.to_datetime(df['trade_date']).max().date()} | "
          f"特征 {len(cols)}/{len(m['feature_cols'])}")


if __name__ == "__main__":
    main()
