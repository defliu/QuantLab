# coding: utf-8
"""阶段4：特征扩展 —— 接入财务质量 + 事件催化（资金/催化类因子）。

在阶段0的20个量价特征基础上，新增两类共15个特征：
  A) 财务质量/增长（来自 E:/astock/finance/fina_indicator.parquet）
     fin_roe / fin_gross_margin / fin_netprofit_margin / fin_assets_turn /
     fin_ocfps / fin_cfps / fin_ocf_to_profit / fin_netprofit_yoy / fin_tr_yoy
     对齐：按 公告日(ann_date) <= T 的最近一期（merge_asof backward，防未来函数）
  B) 事件催化（业绩预告/快报/分红/股本变动）
     fc_days_since / fc_force / fc_pchange   (forecast 业绩预告)
     ex_days_since / ex_yoy                  (express 业绩快报)
     dv_year_sum                             (dividend 近365天税后现金分红)
     sc_days_since / sc_is_unlock            (share_change_event 解禁/增发)
     对齐：按事件日 <= T 的最近一次，窗口外(>90天) 置 0

用法：
  python build_features_v2.py --limit 200 ...   # 调试
  python build_features_v2.py                    # 全量
输出：
  data/feature_panel_v2.parquet   （35特征 + label + fwd_ret）
  data/features_v2.json
"""
import argparse
import json
import os
import numpy as np
import pandas as pd

import data_config as DC

HERE = DC.PROJECT_DIR
DATA = DC.MAIN_DAILY
UNIVERSE = DC.UNIVERSE
FIN = DC.FIN_FINA_INDICATOR
FORECAST = DC.FIN_FORECAST
EXPRESS = DC.FIN_EXPRESS
DIVIDEND = DC.FIN_DIVIDEND
SHARE_CHANGE = DC.FIN_SHARE_CHANGE
OUT_DIR = DC.DATA_DIR
OUT_PANEL = os.path.join(OUT_DIR, "feature_panel_v2.parquet")
OUT_META = os.path.join(OUT_DIR, "features_v2.json")

RAW_COLS = [
    "close", "pct_chg", "vol", "amount", "turnover_rate", "volume_ratio",
    "pe_ttm", "pb", "dv_ttm", "circ_mv", "is_st",
]

# 阶段0的量价特征（保持不变）
BASE_FEATURES = [
    "mom_5", "mom_10", "mom_20", "mom_60", "pos_250", "dist_250_low",
    "volume_ratio", "vol_ratio_5_20", "amount_ma5", "turn_ma5",
    "above_ma20", "above_ma60", "rsi6", "macd_hist", "vol20",
    "log_mv", "pe_ttm", "pb", "dv_ttm", "rel_mom_20",
]
# 阶段4新增：财务质量/增长
FIN_FEATURES = [
    "fin_roe", "fin_gross_margin", "fin_netprofit_margin", "fin_assets_turn",
    "fin_ocfps", "fin_cfps", "fin_ocf_to_profit",
    "fin_netprofit_yoy", "fin_tr_yoy", "fin_dt_netprofit_yoy",
]
# 阶段4新增：事件催化
EVENT_FEATURES = [
    "fc_days_since", "fc_force", "fc_pchange",
    "ex_days_since", "ex_yoy",
    "dv_year_sum",
    "sc_days_since", "sc_is_unlock",
]
ALL_FEATURES = BASE_FEATURES + FIN_FEATURES + EVENT_FEATURES

# 业绩预告类型 -> 利好分
FORECAST_FORCE = {
    "预增": 2.0, "扭亏": 2.0, "略增": 1.0, "续盈": 1.0,
    "略减": -1.0, "预减": -1.0, "首亏": -2.0, "续亏": -2.0, "减亏": 1.0,
}
EVENT_WINDOW = 90   # 事件窗口（天），窗口外事件特征置0
DIV_WINDOW = 365    # 分红累计窗口（天）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--panel_start", default="2019-01-01")
    ap.add_argument("--fwd", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---------- 1. 日线 + 基础量价特征（复用阶段0逻辑） ----------
    print("[1/6] 读取行情并构造基础量价特征 ...")
    df = pd.read_parquet(DATA, columns=RAW_COLS)
    df = df[pd.to_datetime(df.index.get_level_values("trade_date")) >= args.start]
    uni = pd.read_csv(UNIVERSE)
    uni_codes = set(uni[uni["enabled"] == True]["code"].astype(str).tolist())
    df = df[df.index.get_level_values("ts_code").isin(uni_codes)]
    df = df[df["is_st"] == 0]
    codes = df.index.get_level_values("ts_code")
    df = df[~codes.str.startswith("688") & ~codes.str.startswith(("4", "8"))]
    if args.limit > 0:
        keep = list(dict.fromkeys(df.index.get_level_values("ts_code")))[: args.limit]
        df = df[df.index.get_level_values("ts_code").isin(keep)]
    for c in df.columns:
        if df[c].dtype == "float64":
            df[c] = df[c].astype("float32")
    df = df.reset_index()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    print(f"    行情行数: {len(df):,}  股票数: {df['ts_code'].nunique()}")

    close = df["close"].astype(float)
    gkey = df["ts_code"]

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
    feats["volume_ratio"] = df["volume_ratio"]
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

    # ---------- 2. 财务质量/增长（asof 对齐到公告日） ----------
    print("[2/6] 构造财务质量/增长特征 ...")
    RAW_FIN_COLS = [
        "roe", "gross_margin", "netprofit_margin", "assets_turn",
        "ocfps", "cfps", "ocf_to_profit", "netprofit_yoy", "tr_yoy", "dt_netprofit_yoy",
    ]
    fin = pd.read_parquet(FIN, columns=["ts_code", "end_date", "ann_date"] + RAW_FIN_COLS)
    fin = fin.rename(columns=dict(zip(RAW_FIN_COLS, FIN_FEATURES)))
    fin = fin.dropna(subset=["ann_date"])
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
    fin = fin.dropna(subset=["ann_date"]).copy()
    fin["ts_code"] = fin["ts_code"].astype(str)
    fin = fin.sort_values(["ts_code", "ann_date"])
    fin_groups = {k: g for k, g in fin.groupby("ts_code")}

    fin_vals = {}
    for col in FIN_FEATURES:
        fin_vals[col] = np.full(len(df), np.nan, dtype="float32")
    fin_days = np.full(len(df), 9999, dtype="int32")

    grp_idx = df.groupby("ts_code").indices
    for code, idx in grp_idx.items():
        sub = df.iloc[idx]
        f = fin_groups.get(code)
        if f is None or len(f) == 0:
            continue
        m = pd.merge_asof(
            sub[["trade_date"]], f[["ann_date"] + FIN_FEATURES],
            left_on="trade_date", right_on="ann_date", direction="backward",
        )
        for col in FIN_FEATURES:
            fin_vals[col][idx] = m[col].astype("float32").values
        # 用 numpy 计算天数差（避免 Series 按 index 对齐导致长度翻倍）
        td = (sub["trade_date"].values - m["ann_date"].values).astype("timedelta64[D]").astype("float64")
        fin_days[idx] = np.where(np.isnan(td), 9999, td).astype("int32")
    for col in FIN_FEATURES:
        feats[col] = fin_vals[col]
    feats["fin_days_since"] = fin_days
    # 财务报告太旧（>400天）视为缺失
    stale = fin_days > 400
    for col in FIN_FEATURES:
        feats[col] = feats[col].mask(stale)

    # ---------- 3. 事件催化：业绩预告 / 快报 ----------
    print("[3/6] 构造业绩预告/快报事件特征 ...")

    def event_merge(ev_df, ev_cols, ev_key):
        """按 ts_code 循环 merge_asof 取最近一次事件，返回 {col: ndarray} 与 days_since。"""
        ev = ev_df.copy()
        ev["ts_code"] = ev["ts_code"].astype(str)
        ev[ev_key] = pd.to_datetime(ev[ev_key], errors="coerce")
        ev = ev.dropna(subset=[ev_key])
        ev = ev.sort_values(["ts_code", ev_key])
        groups = {k: g for k, g in ev.groupby("ts_code")}
        out = {c: np.full(len(df), 0.0, dtype="float32") for c in ev_cols}
        days = np.full(len(df), 9999, dtype="int32")
        grp_idx = df.groupby("ts_code").indices
        for code, idx in grp_idx.items():
            g = groups.get(code)
            if g is None or len(g) == 0:
                continue
            sub = df.iloc[idx]
            m = pd.merge_asof(
                sub[["trade_date"]], g[[ev_key] + ev_cols],
                left_on="trade_date", right_on=ev_key, direction="backward",
            )
            td = (sub["trade_date"].values - m[ev_key].values).astype("timedelta64[D]").astype("float64")
            days[idx] = np.where(np.isnan(td), 9999, td).astype("int32")
            for c in ev_cols:
                out[c][idx] = m[c].astype("float32").values
        return out, days

    # 业绩预告
    fc = pd.read_parquet(FORECAST, columns=["ts_code", "ann_date", "type", "p_change_max"])
    fc["force"] = fc["type"].map(FORECAST_FORCE).fillna(0.0).astype("float32")
    fc = fc[["ts_code", "ann_date", "force", "p_change_max"]].rename(columns={"p_change_max": "pchange"})
    fc_res, fc_days = event_merge(fc, ["force", "pchange"], "ann_date")
    feats["fc_days_since"] = fc_days
    feats["fc_force"] = np.where(fc_days <= EVENT_WINDOW, fc_res["force"], 0.0)
    feats["fc_pchange"] = np.where(fc_days <= EVENT_WINDOW, fc_res["pchange"], 0.0)

    # 业绩快报
    ex = pd.read_parquet(EXPRESS, columns=["ts_code", "ann_date", "yoy_net_profit"])
    ex_res, ex_days = event_merge(ex.rename(columns={"yoy_net_profit": "yoy"}), ["yoy"], "ann_date")
    feats["ex_days_since"] = ex_days
    feats["ex_yoy"] = np.where(ex_days <= EVENT_WINDOW, ex_res["yoy"], 0.0)

    # ---------- 4. 事件催化：分红 / 股本变动 ----------
    print("[4/6] 构造分红/股本变动特征 ...")
    dv = pd.read_parquet(DIVIDEND, columns=["ts_code", "ann_date", "cash_div_tax"])
    dv["ann_date"] = pd.to_datetime(dv["ann_date"], errors="coerce")
    dv = dv.dropna(subset=["ann_date"]).copy()
    dv["ts_code"] = dv["ts_code"].astype(str)
    # 近 DIV_WINDOW 天税后现金分红合计
    dv_sum = np.zeros(len(df), dtype="float32")
    dv_groups = {k: g for k, g in dv.sort_values(["ts_code", "ann_date"]).groupby("ts_code")}
    grp_idx = df.groupby("ts_code").indices
    for code, idx in grp_idx.items():
        g = dv_groups.get(code)
        if g is None or len(g) == 0:
            continue
        sub = df.iloc[idx]
        pos = np.searchsorted(g["ann_date"].values, sub["trade_date"].values, side="right")
        total = np.zeros(len(sub), dtype="float32")
        gdates = g["ann_date"].values
        gcash = g["cash_div_tax"].fillna(0.0).to_numpy()
        for i, p in enumerate(pos):
            lo = max(0, p - 30)  # 最多回溯30条分红记录，窗口内累计
            seg = gcash[lo:p]
            segd = (sub["trade_date"].values[i] - gdates[lo:p]).astype("timedelta64[D]").astype(int)
            total[i] = seg[(segd <= DIV_WINDOW) & (segd >= 0)].sum()
        dv_sum[idx] = total
    feats["dv_year_sum"] = dv_sum

    # 股本变动（解禁/增发）
    sc = pd.read_parquet(SHARE_CHANGE, columns=["ts_code", "change_date", "change_reason"])
    sc["change_date"] = pd.to_datetime(sc["change_date"], errors="coerce")
    sc = sc.dropna(subset=["change_date"]).copy()
    sc["ts_code"] = sc["ts_code"].astype(str)
    reason = sc["change_reason"].fillna("")
    sc["is_unlock"] = (reason.str.contains("限售|增发|非公开|配股", na=False)).astype("float32")
    sc = sc[["ts_code", "change_date", "is_unlock"]]
    sc_res, sc_days = event_merge(sc, ["is_unlock"], "change_date")
    feats["sc_days_since"] = sc_days
    feats["sc_is_unlock"] = np.where(sc_days <= EVENT_WINDOW, sc_res["is_unlock"], 0.0)

    # ---------- 5. 标签 + 保存 ----------
    print("[5/6] 构造标签并过滤 ...")
    fwd_ret = gshift(close, -args.fwd) / close - 1.0
    feats["fwd_ret"] = fwd_ret
    feats["label"] = (fwd_ret > 0.0).astype("int8")
    feats["trade_date"] = df["trade_date"]
    feats["ts_code"] = df["ts_code"]

    panel = feats[feats["trade_date"] >= pd.Timestamp(args.panel_start)].copy()
    panel = panel.replace([np.inf, -np.inf], np.nan)
    before = len(panel)
    # 量价特征必须有值；财务/事件特征允许部分缺失（树模型可处理NaN）
    panel = panel.dropna(subset=BASE_FEATURES).reset_index(drop=True)
    print(f"    过滤前: {before:,} -> 过滤后: {len(panel):,}")

    print("[6/6] 保存 ...")
    panel.to_parquet(OUT_PANEL, index=False)
    meta = {
        "feature_cols": ALL_FEATURES,
        "base_features": BASE_FEATURES,
        "fin_features": FIN_FEATURES,
        "event_features": EVENT_FEATURES,
        "label_col": "label",
        "fwd_ret_col": "fwd_ret",
        "fwd_days": args.fwd,
        "n_rows": len(panel),
        "n_stocks": int(panel["ts_code"].nunique()),
        "date_range": [str(panel["trade_date"].min()), str(panel["trade_date"].max())],
    }
    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("    保存到:", OUT_PANEL)
    print("    特征总数:", len(ALL_FEATURES), "（基础", len(BASE_FEATURES), "+ 财务", len(FIN_FEATURES), "+ 事件", len(EVENT_FEATURES), "）")
    print("    面板日期:", panel["trade_date"].min(), "->", panel["trade_date"].max())


if __name__ == "__main__":
    main()
