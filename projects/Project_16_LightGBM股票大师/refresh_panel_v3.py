# coding: utf-8
"""周更面板刷新：合并主库+增量 → 重建 v2 → 切片 v3（供 deploy_predict / train_optuna 使用）。

背景：2026-08-28 发现 feature_panel_v3.parquet 停在 8/14 —— 周更 retrain 只跑 build_features_v2
（写 v2 面板），而 v3 面板要靠 WORKFLOW_DEPLOY.md 的一行手工命令切片，不在任何自动化里。
本脚本把「合并增量 + 重建 v2 + 切片 v3」固化，供 run_scheduled.ps1 的 retrain 模式调用。

用法：
  python refresh_panel_v3.py
注意：
  - 主数据 D:/astock 只读，绝不修改；合并日线写 data_live/merged_daily_full.parquet（临时）
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
    # 主库 (trade_date, ts_code) 有序，groupby.tail 取每股最后一行=最新日；避免全表 sort 大数组拷贝
    main_last = main.groupby("ts_code", sort=False).tail(1).set_index("ts_code")
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
    del main
    # 主库(≤主库最后日)与增量(>主库最后日)日期不重叠，正常无重复；
    # 仅对 key 做 duplicated 检测，避免 drop_duplicates 整表 3GB 大数组分配（2026-09-02 修复 _ArrayMemoryError）
    dup_key = merged.duplicated(subset=["ts_code", "trade_date"], keep="last")
    if dup_key.any():
        merged = merged[~dup_key]
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
    feat_all = m["feature_cols"]
    cols = [c for c in feat_all if c in df.columns]
    missing = [c for c in feat_all if c not in df.columns]
    if missing:
        # V2.0 起 33 特征集含 industry_mom20/turnover_rank，build_features_v2 不产出，此处补算（近似口径，与重建脚本一致）
        print(f"  !! v2 面板缺特征，补算: {missing}")
        merged = pd.read_parquet(DC.MAIN_DAILY).reset_index()
        merged["trade_date"] = pd.to_datetime(merged["trade_date"])
        if "turnover_rank" in missing:
            merged["turnover_rank"] = merged.groupby("trade_date")["turnover_rate"].rank(pct=True)
            df = df.merge(merged[["ts_code", "trade_date", "turnover_rank"]], on=["ts_code", "trade_date"], how="left")
            cols.append("turnover_rank")
        if "industry_mom20" in missing:
            td = pd.read_parquet(os.path.join(DC.ASTOCK_DIR, "board", "ths_daily.parquet")).reset_index()
            td["trade_date"] = pd.to_datetime(td["trade_date"])
            td["ts_code"] = td["ts_code"].astype(str)
            td = td[td["ts_code"].str.startswith("881")].sort_values(["ts_code", "trade_date"])
            td["ind_mom20"] = td.groupby("ts_code")["close"].transform(lambda s: s / s.shift(20) - 1.0)
            ind_map = td.set_index(["ts_code", "trade_date"])["ind_mom20"]
            comp = pd.read_parquet(os.path.join(DC.ASTOCK_DIR, "board", "source",
                                                "parquet行业概念板块全量更新到20260814", "parquet",
                                                "行业概念板块", "行业板块成分汇总_同花顺.parquet"))
            comp["股票代码"] = comp["股票代码"].astype(str)
            comp["指数代码"] = comp["指数代码"].astype(str)
            stk2ind = comp.drop_duplicates("股票代码").set_index("股票代码")["指数代码"]
            merged["ths_ind"] = merged["ts_code"].map(stk2ind)
            sub = merged[["ths_ind", "trade_date"]].copy()
            sub["ts_code"] = sub["ths_ind"]
            merged["industry_mom20"] = ind_map.reindex(pd.MultiIndex.from_frame(sub[["ts_code", "trade_date"]])).values
            df = df.merge(merged[["ts_code", "trade_date", "industry_mom20"]], on=["ts_code", "trade_date"], how="left")
            cols.append("industry_mom20")
    df = df[cols + ["trade_date", "ts_code", "label", "fwd_ret"]]
    df.to_parquet(os.path.join(DATA, "feature_panel_v3.parquet"))
    print(f"  v3 面板已切片 | 最新日 {pd.to_datetime(df['trade_date']).max().date()} | "
          f"特征 {len(cols)}/{len(feat_all)}")


if __name__ == "__main__":
    main()
