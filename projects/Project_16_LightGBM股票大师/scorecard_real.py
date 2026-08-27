# coding: utf-8
"""真实版评分卡（用于 v3_sc 面板回测）：F2/F5 换成真实数据口径，F6 用实时 PE+换手。

对应 review_full.py 实盘 8/21 真实口径：
  F2 = RF.score_f2(main_net, volume_ratio)   # 主力净额(真实) + 量比
  F5 = RF.score_f5(industry_pct)             # 所属行业当日涨幅(真实)
  F6 = RF.score_f6(pe_ttm, turnover_rate)    # 实时 PE + 换手（与原 scan_rotate_cost 的 F6_new 完全一致）
  F1/F3/F4 沿用 DP.compute_scorecard 代理分（无真实列，保持不变）

总分拼分逻辑与 scan_rotate_cost.py 完全一致（W 权重 ×10 得百分制）：
  total_new_real = (W1*F1 + W2*F2 + W3*F3 + W4*F4 + W5*F5) * 10 + W6*F6 * 10

用法（由 scan_rotate_cost_real.py 调用）：
  sc = compute_real_scorecard(day, est)
  day["total_new"] = sc["total_new_real"].values
"""
import numpy as np
import pandas as pd

import deploy_predict as DP
import review_full as RF

SC_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def _f(x):
    """安全转 float，None/NaN 统一成 np.nan。"""
    try:
        v = float(x)
        return np.nan if np.isnan(v) else v
    except (TypeError, ValueError):
        return np.nan


def compute_real_scorecard(day, est=None):
    """真实评分卡。

    day 需含特征列（F1/F3/F4 用）+ main_net + industry_pct + volume_ratio；
    est 可选：daily 主库按 (trade_date, ts_code) 重索引的结果，提供 pe_ttm / turnover_rate
    （T 日实时口径，与原 scan_rotate_cost 的 F6_new 同源）。est 缺失时 F6 回退面板 pe_ttm / turn_ma5。

    返回 DataFrame（index 与 day 一致）：F1-F6 + total_new_real。
    """
    sc = DP.compute_scorecard(day)  # 代理分 F1-F6 + total
    out = pd.DataFrame(index=day.index)
    out["F1"] = sc["F1"].values
    out["F3"] = sc["F3"].values
    out["F4"] = sc["F4"].values

    # F2 真实版：主力净额 + 量比（无真实主力净额 → 中性 5.0）
    mn = day["main_net"].map(_f).values if "main_net" in day.columns else np.full(len(day), np.nan)
    vr = day["volume_ratio"].map(_f).values if "volume_ratio" in day.columns else np.full(len(day), np.nan)
    out["F2"] = [RF.score_f2(a, b) for a, b in zip(mn, vr)]

    # F5 真实版：行业当日涨幅（无行业数据 → 中性 5.0）
    ind = day["industry_pct"].map(_f).values if "industry_pct" in day.columns else np.full(len(day), np.nan)
    out["F5"] = [RF.score_f5(a) for a in ind]

    # F6 真实版：PE + 换手（优先 est 主库实时口径，缺失回退面板特征）
    if est is not None and "pe_ttm" in est.columns:
        pe = est["pe_ttm"].map(_f).values
        tu = est["turnover_rate"].map(_f).values
    else:
        pe = day["pe_ttm"].map(_f).values if "pe_ttm" in day.columns else np.full(len(day), np.nan)
        tu = day["turn_ma5"].map(_f).values if "turn_ma5" in day.columns else np.full(len(day), np.nan)
    out["F6"] = [RF.score_f6(a, b) for a, b in zip(pe, tu)]

    total = (sum(SC_WEIGHTS[k] * out[k] for k in ("F1", "F2", "F3", "F4", "F5")) * 10.0
             + SC_WEIGHTS["F6"] * out["F6"] * 10.0)
    out["total_new_real"] = total
    return out


if __name__ == "__main__":
    # 自检：用面板某日数据跑一遍
    import json
    import os
    import data_config as DC
    import lightgbm as lgb

    panel = pd.read_parquet(os.path.join(DC.DATA_DIR, "feature_panel_v3_sc.parquet"))
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    day = panel[panel["trade_date"] == "2026-08-13"].copy()
    meta = json.load(open(os.path.join(DC.DATA_DIR, "features_v3_enh.json"), encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    idx = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"]])
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.set_index(["trade_date", "ts_code"])
    est = daily.reindex(idx)
    res = compute_real_scorecard(day, est)
    print(res[["F1", "F2", "F3", "F4", "F5", "F6", "total_new_real"]].describe().to_string())
    print("\nNaN count per col:\n", res[["F2", "F5", "F6"]].isna().sum())
    print("\n样例:")
    print(res[["F1", "F2", "F3", "F4", "F5", "F6", "total_new_real"]].head(5).to_string())
