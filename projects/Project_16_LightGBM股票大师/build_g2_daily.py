# -*- coding: utf-8 -*-
"""g2 盘后链路（步骤①）—— 为最新交易日构建 g2 模型的 43 特征快照。

独立于 V1.1：不碰 run_scheduled.ps1 / deploy_predict.py / feature_panel_v3.parquet / lgb_model_v3.txt。
数据分层（与 merge_live_features 一致）：
  主库 E:/astock（周更权威，只读）+ data_live/incremental_daily.parquet（每日增量）
  → 重算 19 个量价特征（真实最新日）
  基础面板 feature_panel_v3_enh.parquet asof → 14 个财务/事件/行业特征（慢变量近似）
  E:/astock 周更因子（moneyflow / lhb / northbound / research / board）→ 10 个 g2 新因子（最新可用≤目标日，前向填充，诚实标注滞后）

输出：data_live/g2_latest_features.parquet（43 特征，目标日，与 features_v3_g2_strong_real 同序）

用法：python build_g2_daily.py --date 2026-08-25   （缺省取增量库最新日）
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

import data_config as DC
import g2_realtime as RT

HERE = DC.PROJECT_DIR
DATA = DC.DATA_DIR
LIVE = DC.LIVE_DIR
ASTOCK = DC.ASTOCK_DIR
INCR = os.path.join(LIVE, "incremental_daily.parquet")
PANEL_ENH = os.path.join(DATA, "feature_panel_v3_enh.parquet")
G2_META = os.path.join(DATA, "features_v3_g2_strong_real_20260825.json")
OUT = os.path.join(LIVE, "g2_latest_features.parquet")

RAW_COLS = ["close", "pct_chg", "vol", "amount", "turnover_rate", "volume_ratio",
            "pe_ttm", "pb", "dv_ttm", "circ_mv", "is_st"]


def compute_price_features(df):
    """复用 merge_live_features 的量价特征重算（真实最新日）。"""
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


def asof_latest(df, cols, fill=0.0):
    """取每股 trade_date<=target 的最后一行（asof），缺失填 fill。df 需含 trade_date/ts_code。"""
    g = df.groupby("ts_code")[cols].last().fillna(fill)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日，缺省取增量库最新日")
    ap.add_argument("--realtime-f2", default=None,
                    help="对指定 ts_code 列表（逗号分隔）实时采集当日主力净额覆盖周更值；缺省仅周更")
    ap.add_argument("--realtime-f2-file", default=None,
                    help="从文件（每行一个 ts_code）读取实时采集 F2 的股票池")
    args = ap.parse_args()
    os.makedirs(LIVE, exist_ok=True)
    feat_cols = json.load(open(G2_META, encoding="utf-8"))["feature_cols"]
    base_ev = ["sc_is_unlock", "fin_ocf_to_profit", "sc_days_since", "fin_dt_netprofit_yoy",
               "fc_days_since", "fin_netprofit_yoy", "fc_force", "ex_days_since", "fc_pchange",
               "dv_year_sum", "ex_yoy", "industry_mom20", "turnover_rank"]
    new_factors = ["lhb_net", "lhb_count", "north_chg", "rc_rating", "rc_num",
                   "mf_main_net", "mf_elg_net", "mf_main_ratio", "ind_pct_ths", "ind_net_ths"]

    print("[1/6] 构造合并行情（主库 + 增量） ...")
    main_df = pd.read_parquet(DC.MAIN_DAILY, columns=RAW_COLS).reset_index()
    main_df["trade_date"] = pd.to_datetime(main_df["trade_date"])
    main_df["ts_code"] = main_df["ts_code"].astype(str)
    incr = pd.read_parquet(INCR)
    incr["trade_date"] = pd.to_datetime(incr["trade_date"])
    incr["ts_code"] = incr["ts_code"].astype(str)
    target = pd.Timestamp(args.date) if args.date else incr["trade_date"].max()
    print(f"    主库到 {main_df['trade_date'].max().date()} | 增量到 {incr['trade_date'].max().date()} | 目标 {target.date()}")
    main_last = main_df.sort_values(["ts_code", "trade_date"]).groupby("ts_code").tail(1).set_index("ts_code")
    incr_full = incr.copy()
    fill_cols = ["pct_chg", "turnover_rate", "volume_ratio", "pe_ttm", "pb", "dv_ttm", "circ_mv", "is_st"]
    for c in fill_cols:
        if c in main_last.columns:
            incr_full[c] = incr_full["ts_code"].map(main_last[c])
    merged = pd.concat([main_df, incr_full[[c for c in ["ts_code", "trade_date"] + RAW_COLS if c in incr_full.columns]]], ignore_index=True)
    for c in RAW_COLS:
        if c not in merged.columns:
            merged[c] = np.nan
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last").sort_values(["ts_code", "trade_date"])
    merged = merged[merged["trade_date"] <= target]
    print(f"    合并后 {len(merged):,} 行 | 最新可用 {merged['trade_date'].max().date()}")

    print("[2/6] 重算量价特征（真实最新日） ...")
    feats = compute_price_features(merged)
    latest = merged[merged["trade_date"] == merged["trade_date"].max()][["ts_code", "trade_date"]].copy()
    for c in feats.columns:
        latest[c] = feats.loc[latest.index, c].values

    print("[3/6] 财务/事件/行业特征（基础面板 asof 近似） ...")
    panel = pd.read_parquet(PANEL_ENH)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    base = panel[panel["trade_date"] == panel["trade_date"].max()].set_index("ts_code")
    for c in base_ev:
        if c in base.columns:
            latest[c] = latest["ts_code"].map(base[c])
        else:
            latest[c] = np.nan

    print("[4/6] g2 新因子（东财当日 → MCP 当日 → E:/astock 周更回退） ...")
    # 龙虎榜净买/次数：优先东财 datacenter 当日（方案B全自动，复刻 E:/astock 去重口径），
    # 次选悟道 MCP 当日文件（方案A），均缺失则回退 E:/astock 周更
    lhb = RT.fetch_lhb_eastmoney(target.strftime("%Y-%m-%d"))
    src = "东财"
    if not lhb:
        lhb = RT.fetch_lhb_from_mcp_file(target.strftime("%Y-%m-%d"), lhb_dir=os.path.join(DATA, "real"))
        src = "MCP"
    if lhb:
        latest["lhb_net"] = latest["ts_code"].map(lambda c: (lhb.get(c) or {}).get("lhb_net", 0.0)).fillna(0.0)
        latest["lhb_count"] = latest["ts_code"].map(lambda c: (lhb.get(c) or {}).get("lhb_count", 0)).fillna(0)
        print(f"    龙虎榜用{src}当日数据：覆盖 {len(lhb)} 只（{target.date()}）")
    else:
        tl = pd.read_parquet(os.path.join(ASTOCK, "lhb", "top_list.parquet")).reset_index()
        tl["trade_date"] = pd.to_datetime(tl["trade_date"])
        tl = tl[tl["trade_date"] <= target]
        tl_g = tl.groupby(["ts_code", "trade_date"]).agg(lhb_net=("net_amount", "sum"), lhb_count=("net_amount", "size")).reset_index()
        for c in ["lhb_net", "lhb_count"]:
            latest[c] = latest["ts_code"].map(asof_latest(tl_g, [c]).get(c, 0.0) if len(tl_g) else {}).fillna(0.0)
        print("    龙虎榜无当日源，回退 E:/astock 周更（可能滞后数天）")
    # 北向持股变动
    # 北向持股变动（上游 2024-08 起个股持股改季频披露，无当日值，保持 E:/astock 周更/季频快照）
    try:
        hk = pd.read_parquet(os.path.join(ASTOCK, "northbound", "hk_hold_full.parquet"))
        hk["trade_date"] = pd.to_datetime(hk["trade_date"])
        hk = hk[hk["trade_date"] <= target].sort_values(["ts_code", "trade_date"])
        hk["north_chg"] = hk.groupby("ts_code")["ratio"].diff()
        hk_chg = hk[["ts_code", "trade_date", "north_chg"]].dropna(subset=["north_chg"])
        latest["north_chg"] = latest["ts_code"].map(asof_latest(hk_chg, ["north_chg"]).get("north_chg", 0.0) if len(hk_chg) else {}).fillna(0.0)
    except Exception as e:
        latest["north_chg"] = 0.0
        print(f"    北向数据读取失败（{e}），置 0")
    # 研报评级/数量：优先东财 reportapi 当日（已落地），缺失回退 E:/astock 周更
    rc_em = RT.fetch_research_eastmoney(target.strftime("%Y-%m-%d"))
    if rc_em:
        latest["rc_rating"] = latest["ts_code"].map(lambda c: (rc_em.get(c) or {}).get("rc_rating", 0.0)).fillna(0.0)
        latest["rc_num"] = latest["ts_code"].map(lambda c: (rc_em.get(c) or {}).get("rc_num", 0)).fillna(0)
        print(f"    研报用东财当日数据：覆盖 {len(rc_em)} 只（{target.date()}）")
    else:
        rc = pd.read_parquet(os.path.join(ASTOCK, "research", "report_rc_daily.parquet")).reset_index()
        rc["trade_date"] = pd.to_datetime(rc["report_date"])
        rc["rc_rating_up"] = rc["rating"].map({"买入": 2, "增持": 1, "持有": 0, "中性": -1, "减持": -2, "卖出": -3})
        rc = rc[rc["trade_date"] <= target]
        rc_g = rc.groupby(["ts_code", "trade_date"]).agg(rc_num=("rating", "size"), rc_rating=("rc_rating_up", "mean")).reset_index()
        for c in ["rc_rating", "rc_num"]:
            latest[c] = latest["ts_code"].map(asof_latest(rc_g, [c]).get(c, 0.0) if len(rc_g) else {}).fillna(0.0)
        print("    研报无当日源，回退 E:/astock 周更（可能滞后数天）")
    # 个股五档资金流（moneyflow）
    mf = pd.read_parquet(os.path.join(ASTOCK, "moneyflow", "moneyflow.parquet")).reset_index()
    mf["trade_date"] = pd.to_datetime(mf["trade_date"])
    mf = mf[mf["trade_date"] <= target]
    mf["mf_main_net"] = mf["buy_lg_amount"] + mf["buy_elg_amount"] - mf["sell_lg_amount"] - mf["sell_elg_amount"]
    mf["mf_elg_net"] = mf["buy_elg_amount"] - mf["sell_elg_amount"]
    tot = mf[["buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount"]].sum(axis=1)
    mf["mf_main_ratio"] = np.where(tot > 0, mf["mf_main_net"] / tot, np.nan)
    for c in ["mf_main_net", "mf_elg_net", "mf_main_ratio"]:
        latest[c] = latest["ts_code"].map(asof_latest(mf, [c]).get(c, np.nan) if len(mf) else {}).fillna(0.0)
    # 同花顺板块涨幅（F5：增量库自算当日行业涨幅，替代 ths_daily 周更）与行业资金流（周更）
    comp = pd.read_parquet(os.path.join(ASTOCK, "board", "source", "parquet行业概念板块全量更新到20260814",
                                        "parquet", "行业概念板块", "行业板块成分汇总_同花顺.parquet"))
    comp["股票代码"] = comp["股票代码"].astype(str)
    comp["指数代码"] = comp["指数代码"].astype(str)
    stk2ind = comp.set_index("股票代码")["指数代码"].to_dict()
    # 当日行业涨幅：用增量库当日成分股行情自算（成交额加权），与回测 ind_pct_ths 同属同花顺 881 分类
    incr_day = incr[incr["trade_date"] == target].copy()
    ind_pct_daily = RT.compute_industry_pct_daily(incr_day, comp)
    if ind_pct_daily:
        print(f"    F5 当日行业涨幅自算成功：覆盖 {len(ind_pct_daily)} 个 881 板块（增量库 {target.date()}）")
    else:
        print("    !! F5 当日自算为空，回退 ths_daily 周更")
        td = pd.read_parquet(os.path.join(ASTOCK, "board", "ths_daily.parquet")).reset_index()
        td["trade_date"] = pd.to_datetime(td["trade_date"])
        td["ts_code"] = td["ts_code"].astype(str)
        td = td[(td["ts_code"].str.startswith("881")) & (td["trade_date"] <= target)]
        ind_pct = td[["trade_date", "ts_code", "pct_change"]].rename(columns={"ts_code": "ths_ind", "pct_change": "ind_pct_ths"})
        ind_pct_l = ind_pct.groupby("ths_ind").last()
        ind_pct_daily = ind_pct_l["ind_pct_ths"].to_dict()
    latest["ths_ind"] = latest["ts_code"].map(stk2ind)
    latest["ind_pct_ths"] = latest["ths_ind"].map(ind_pct_daily).fillna(0.0)
    iths = pd.read_parquet(os.path.join(ASTOCK, "board_fundflow", "moneyflow_ind_ths.parquet")).reset_index()
    iths["trade_date"] = pd.to_datetime(iths["trade_date"])
    iths["ts_code"] = iths["ts_code"].astype(str)
    iths = iths[iths["trade_date"] <= target]
    iths_l = iths.groupby("ts_code")["net_amount"].last().rename("ind_net_ths")
    latest["ind_net_ths"] = latest["ths_ind"].map(iths_l).fillna(0.0)

    # F2 实时覆盖：对指定股票池实时采集当日主力净额，替换周更值（新浪资金流逐股，只对候选池用）
    rt_codes = []
    if args.realtime_f2:
        rt_codes += [c.strip() for c in args.realtime_f2.split(",") if c.strip()]
    if args.realtime_f2_file:
        if os.path.exists(args.realtime_f2_file):
            with open(args.realtime_f2_file, encoding="utf-8") as f:
                rt_codes += [ln.strip() for ln in f if ln.strip()]
    if rt_codes:
        rt_codes = list(dict.fromkeys(rt_codes))
        print(f"    F2 实时采集 {len(rt_codes)} 只（新浪当日主力净额，逐股） ...")
        rt = RT.fetch_main_net_sina(rt_codes, target_date=target.strftime("%Y-%m-%d"))
        hit = 0
        for c in rt_codes:
            if c not in rt:
                continue
            idx = latest["ts_code"] == c
            if not idx.any():
                continue
            for col in ("mf_main_net", "mf_elg_net", "mf_main_ratio"):
                latest.loc[idx, col] = rt[c].get(col, np.nan)
            hit += 1
        print(f"    实时覆盖 {hit}/{len(rt_codes)} 只 | 未命中回退周更值")
        # 诚实标注：把实时覆盖的日期写入说明（用增量库目标日）
    else:
        print("    未指定 --realtime-f2，F2 用 E:/astock 周更值（可能滞后数天）")

    print("[5/6] 组装 43 特征并保存 ...")
    latest["trade_date"] = target
    out = latest[["ts_code", "trade_date"] + feat_cols]
    out = out.replace([np.inf, -np.inf], np.nan)
    out.to_parquet(OUT, index=False)
    print(f"    快照: {OUT}  ({len(out):,} 行, {len(feat_cols)} 特征)")

    print("[6/6] 说明")
    print("    - 量价特征为最新真实日（增量库）；财务/事件/行业特征为基础面板 asof 近似")
    print("    - F5 板块涨幅：增量库自算当日行业涨幅（成交额加权，881 板块，与回测同分类）")
    print("    - F2 主力净额：指定 --realtime-f2 时为新浪当日实时（逐股）；否则 E:/astock 周更（可能滞后数天）")
    print("    - 其余 g2 新因子（lhb/北向/研报/行业资金流）为 E:/astock 周更最新可用值（诚实标注滞后）")
    print("    - 主数据 E:/astock 未修改；V1.1 资产未触碰")


if __name__ == "__main__":
    main()
