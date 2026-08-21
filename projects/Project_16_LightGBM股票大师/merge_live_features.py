# coding: utf-8
"""合并视图 v2：主库(历史) + 临时增量库 → 用合并行情重算最新日量价特征。

数据分层：
  主数据 E:/astock（周更权威快照）→ 只读，绝不修改
  临时库 data_live/incremental_daily.parquet（xtdata 每日增量 OHLCV）→ 独立
  本脚本输出 data_live/latest_features.parquet（最新日 v3 特征），供 deploy 预测

特征口径（诚实标注）：
  - 量价特征（mom/MA/RSI/MACD/vol20/pos_250/量比5-20/amount_ma5/rel_mom）：
    用 主库历史序列 + 增量真实 close/vol/amount 重算 → 真实(最新日)
  - volume_ratio / turn_ma5 / pe_ttm / pb / dv_ttm / log_mv（换手/量比/估值）：
    增量库无此字段，用主库 8/14 最后值前向填充 → 近似(中慢速变量)
  - 财务/事件特征（7 个）：用主库面板 8/14 asof 值 → 慢变量近似

用法：
  python merge_live_features.py --date 2026-08-19
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

import data_config as DC

HERE = DC.PROJECT_DIR
LIVE_DIR = DC.LIVE_DIR
MAIN_DAILY = DC.MAIN_DAILY
PANEL_V2 = os.path.join(DC.DATA_DIR, "feature_panel_v2.parquet")
INCR = os.path.join(LIVE_DIR, "incremental_daily.parquet")
META_V3 = os.path.join(DC.DATA_DIR, "features_v3.json")
OUT = os.path.join(LIVE_DIR, "latest_features.parquet")

RAW_COLS = ["close", "pct_chg", "vol", "amount", "turnover_rate", "volume_ratio",
            "pe_ttm", "pb", "dv_ttm", "circ_mv", "is_st"]


def compute_price_features(df):
    """对合并行情 df(已按 ts_code,trade_date 排序) 重算量价特征（复用 build_features_v2 逻辑）。"""
    gkey = df["ts_code"]
    close = df["close"].astype(float)

    def gshift(s, n):
        return s.groupby(gkey).shift(n)

    def groll(s, w, agg="mean"):
        r = s.groupby(gkey).rolling(w, min_periods=w).agg(agg)
        r = r.reset_index(level=0, drop=True)
        r.index = df.index
        return r

    feats = pd.DataFrame(index=df.index)
    feats["mom_5"] = close / gshift(close, 5) - 1.0
    feats["mom_10"] = close / gshift(close, 10) - 1.0
    feats["mom_20"] = close / gshift(close, 20) - 1.0
    feats["mom_60"] = close / gshift(close, 60) - 1.0
    high250 = groll(close, 250, "max")
    low250 = groll(close, 250, "min")
    feats["pos_250"] = close / high250
    feats["dist_250_low"] = close / low250 - 1.0
    feats["volume_ratio"] = df["volume_ratio"].astype(float)
    vol_ma5 = groll(df["vol"].astype(float), 5)
    vol_ma20 = groll(df["vol"].astype(float), 20)
    feats["vol_ratio_5_20"] = vol_ma5 / vol_ma20
    feats["amount_ma5"] = np.log1p(groll(df["amount"].astype(float), 5))
    feats["turn_ma5"] = groll(df["turnover_rate"].astype(float), 5)
    ma20 = groll(close, 20)
    ma60 = groll(close, 60)
    feats["above_ma20"] = close / ma20 - 1.0
    feats["above_ma60"] = close / ma60 - 1.0
    delta = close.groupby(gkey).diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    avg_loss = loss.groupby(gkey).transform(lambda s: s.ewm(alpha=1.0 / 6, min_periods=6).mean())
    feats["rsi6"] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss.replace(0.0, np.nan))
    ema12 = close.groupby(gkey).transform(lambda s: s.ewm(span=12, adjust=False).mean())
    ema26 = close.groupby(gkey).transform(lambda s: s.ewm(span=26, adjust=False).mean())
    dif = ema12 - ema26
    dea = dif.groupby(gkey).transform(lambda s: s.ewm(span=9, adjust=False).mean())
    feats["macd_hist"] = (dif - dea) * 2.0
    ret1 = close / gshift(close, 1) - 1.0
    feats["vol20"] = groll(ret1, 20, "std")
    feats["log_mv"] = np.log(df["circ_mv"].astype(float))
    feats["pe_ttm"] = df["pe_ttm"].astype(float)
    feats["pb"] = df["pb"].astype(float)
    feats["dv_ttm"] = df["dv_ttm"].astype(float)
    mkt_mom20 = feats["mom_20"].groupby(df["trade_date"]).transform("median")
    feats["rel_mom_20"] = feats["mom_20"] - mkt_mom20
    return feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-08-19")
    args = ap.parse_args()
    target = pd.Timestamp(args.date)
    feat_cols = json.load(open(META_V3, encoding="utf-8"))["feature_cols"]

    print("[1/5] 构造合并行情（主库历史 + 增量真实 OHLCV） ...")
    main_df = pd.read_parquet(MAIN_DAILY, columns=RAW_COLS)
    main_df = main_df.reset_index()
    main_df["trade_date"] = pd.to_datetime(main_df["trade_date"])
    main_df["ts_code"] = main_df["ts_code"].astype(str)
    incr = pd.read_parquet(INCR)
    incr["trade_date"] = pd.to_datetime(incr["trade_date"])
    incr["ts_code"] = incr["ts_code"].astype(str)
    print(f"    主库 {len(main_df):,} 行到 {main_df['trade_date'].max().date()} | 增量 {len(incr):,} 行 {incr['trade_date'].min().date()}~{incr['trade_date'].max().date()}")

    # 主库每只最后一日快照（用于填充增量缺失的衍生字段）
    main_last = main_df.sort_values(["ts_code", "trade_date"]).groupby("ts_code").tail(1).set_index("ts_code")
    # 构造增量行：真实 OHLCV + 主库最后值填充其他字段
    incr_full = incr.copy()
    fill_cols = ["pct_chg", "turnover_rate", "volume_ratio", "pe_ttm", "pb", "dv_ttm", "circ_mv", "is_st"]
    fill_map = {c: main_last[c] for c in fill_cols if c in main_last.columns}
    for c, s in fill_map.items():
        incr_full[c] = incr_full["ts_code"].map(s)
    keep_cols = ["ts_code", "trade_date"] + RAW_COLS
    incr_full = incr_full[[c for c in keep_cols if c in incr_full.columns]]
    for c in RAW_COLS:
        if c not in incr_full.columns:
            incr_full[c] = np.nan
    incr_full = incr_full[keep_cols]

    merged = pd.concat([main_df, incr_full], ignore_index=True)
    merged["trade_date"] = pd.to_datetime(merged["trade_date"])
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    merged = merged.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    print(f"    合并后 {len(merged):,} 行 | 最新日 {merged['trade_date'].max().date()}")

    print("[2/5] 重算量价特征（真实增量） ...")
    feats = compute_price_features(merged)
    merged = pd.concat([merged[["ts_code", "trade_date"]], feats], axis=1)
    latest = merged[merged["trade_date"] == target].copy()
    print(f"    目标日 {target.date()} 特征行数: {len(latest):,}")

    print("[3/5] 财务/事件特征 from 主库面板 8/14 asof ...")
    panel = pd.read_parquet(PANEL_V2)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    base = panel[panel["trade_date"] == panel["trade_date"].max()].set_index("ts_code")
    ev_cols = ["sc_is_unlock", "fin_ocf_to_profit", "sc_days_since", "fin_dt_netprofit_yoy",
               "fc_days_since", "fin_netprofit_yoy", "fc_force"]
    for c in ev_cols:
        if c in base.columns:
            latest[c] = latest["ts_code"].map(base[c])
        else:
            latest[c] = np.nan
    latest["trade_date"] = target

    print("[4/5] 组装 v3 特征并保存 ...")
    latest = latest[["ts_code", "trade_date"] + feat_cols]
    latest = latest.replace([np.inf, -np.inf], np.nan)
    latest.to_parquet(OUT, index=False)
    print("    快照:", OUT)
    print("    特征数:", len(feat_cols), "| 行数:", len(latest))

    print("[5/5] 主数据 E:/astock 未修改 ✅（临时库独立）")
    print("    下一步: deploy 可用 --date 2026-08-19 基于此快照预测（需将快照作为面板输入）")


if __name__ == "__main__":
    main()
