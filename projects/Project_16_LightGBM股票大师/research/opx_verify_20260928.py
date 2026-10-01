# -*- coding: utf-8 -*-
"""OP 独立交叉验证脚本（2026-09-28 T-20260928-003）。

独立性声明：本脚本不复用任何历史 op_* 脚本的结论与代码路径，全部从
「生产源码 + 原始数据」重新推导。

验证命题：
  A. 逐行逐字段复现 20260928_g2_top10.csv（live 生产链路）
  B. 当日 Top100 的 F5 分布与对总分的量化影响
  C. 2024-07-01~2026-08-14 红线通过率（生产 Top100 口径）
  D. 绝对阈值 F5 vs rank-F5 的 IS/OOS 超额对比（rank-F5 为本脚本构造的对照口径）
  E. audit_stats_walkforward.align_market 对齐方式核验与量化影响

实现约束（自纠，不引用任何既有报告）：
  - 回测面板 feature_panel_v3_enh2_n3_bt.parquet 原始 schema **不含** prob/total_new
    （二者均为运行时生成：prob=模型推理，total_new=compute_real_scorecard），
    故不能直接取列，必须逐日 predict + 逐日打分。
  - scan_rotate_cost_real.build_per_day 的 per_day 只保留 prob 前 10，
    无法回答「Top100 预选池过红线数」；本脚本自建单次扫描并缓存 Top100 明细。
  - scorecard_real.compute_real_scorecard 内部为逐行 df.apply（无截面归一），
    故「全池打分」与「Top100 打分」逐行数值完全相同，C 命题口径与回测自洽。
  - 生产候选池 = 概率 Top100 → 红线 → total_new Top10；
    回测买入池 = 概率 Top10 → 红线 → total_new Top2。二者口径不同，分别标注。
  - build_per_day 中 market_avgs.append 位于 `len(day) < 20: continue` 之后，
    本脚本如实复刻该顺序（E 命题要用）。

只读约束：不改生产代码、不写 data_live / data/selections / D:/QMT_POOL。
输出：results/XVAL_OP_20260928.md
缓存：research/_opx_cache_20260928.parquet（评分明细）
      research/_opx_mkt_20260928.parquet（市场基准）
      research/_opx_maps_20260928.pkl（行情映射，供 simulate 复用）
"""
import argparse
import datetime
import json
import os
import pickle
import sys
import time
import traceback

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
os.chdir(PROJ)

RESULTS = []
LINES = []

CACHE = os.path.join(PROJ, "research", "_opx_cache_20260928.parquet")
MKT_CACHE = os.path.join(PROJ, "research", "_opx_mkt_20260928.parquet")
MAPS_PKL = os.path.join(PROJ, "research", "_opx_maps_20260928.pkl")

BT_START = "2024-07-01"
BT_END = "2026-08-14"
IS_CUT = pd.Timestamp("2025-07-01")
THRS = (60.0, 58.0, 55.0, 52.0, 50.0)
N_HOLD = 10
SLIP = 0.001
PRE_POOL = 100
TOP10 = 10
TOP_HOLD = 2
REDLINE = 60.0

# 生产/回测口径常量（全部读自源码，不引用任何既有报告数字）
PANEL_PATH = os.path.join(PROJ, "data", "feature_panel_v3_enh2_n3_bt.parquet")
MODEL_PATH = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
META_PATH = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260825.json")

STATE = {"n_short": None, "short_dates": None, "open_map": None}


def emit(title):
    LINES.append("")
    LINES.append("## %s" % title)
    LINES.append("")


def p(s=""):
    print(s, flush=True)
    LINES.append(s)


def rec(topic, conclusion, evidence):
    RESULTS.append((topic, conclusion, evidence))
    p("- **[%s] %s**" % (topic, conclusion))
    p("  - 证据：%s" % evidence)


def rank_f5_of(s):
    """rank-F5（本脚本构造的对照口径，非生产口径）。

    在同一当日 universe 内把 industry_pct 转百分位，映射 5 档。
    与绝对阈值 F5 的区别：只看相对分位，不看行业当日涨跌幅的绝对水平。
    """
    r = s.rank(pct=True)
    out = np.full(len(s), 2.0)
    out[(r > 0.2).values] = 4.0
    out[(r > 0.4).values] = 6.0
    out[(r > 0.6).values] = 8.0
    out[(r > 0.8).values] = 10.0
    out[s.isna().values] = 5.0
    return pd.Series(out, index=s.index)


# ======================================================================
# 命题 A + B：live 生产链路复现
# ======================================================================
def run_live():
    import data_config as DC
    import lightgbm as lgb
    import deploy_predict_g2 as G2
    import scorecard_real as SR

    emit("命题 A：2026-09-28 live 生产链路逐字段独立复现")
    p("```")

    model_path, meta_path = DC.g2_live()
    feat_cols = json.load(open(meta_path, encoding="utf-8"))["feature_cols"]
    p("live 模型指针 -> %s" % os.path.basename(model_path))
    p("meta 特征列数 = %d" % len(feat_cols))

    snap = pd.read_parquet(os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet"))
    snap["trade_date"] = pd.to_datetime(snap["trade_date"])
    target = snap["trade_date"].max()
    day = snap[snap["trade_date"] == target].copy()
    p("快照目标日 = %s，全量行数 = %d" % (target.date(), len(day)))
    p("快照是否唯一索引 = %s" % day.index.is_unique)

    bst = lgb.Booster(model_file=model_path)
    day["prob"] = bst.predict(day[feat_cols].astype("float32").values)
    pre = day.nlargest(100, "prob").copy()
    p("模型 Top100 概率区间 = [%.4f, %.4f]（第100名）" % (pre["prob"].max(), pre["prob"].min()))

    # --- F6 est 命中率判定（复刻 deploy_predict_g2 的 daily_basic 优先 + 命中率门）---
    est = None
    est_src = "无"
    if os.path.exists(DC.DAILY_BASIC):
        # 复刻 deploy_predict_g2 的两段式布局探测：先试扁平列，失败再 reset_index()
        daily = None
        try:
            daily = pd.read_parquet(DC.DAILY_BASIC,
                                    columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
            if "trade_date" not in daily.columns:
                raise KeyError("trade_date 不在数据列，回退旧布局")
        except Exception as e1:
            p("布局探测第 1 段失败（%s），回退 reset_index 布局" % type(e1).__name__)
            try:
                daily = pd.read_parquet(DC.DAILY_BASIC,
                                        columns=["pe_ttm", "turnover_rate"]).reset_index()
            except Exception as e2:
                p("布局探测第 2 段也失败（%s）-> daily=None" % type(e2).__name__)
                daily = None
        if daily is not None:
            daily["trade_date"] = pd.to_datetime(daily["trade_date"])
            daily["ts_code"] = daily["ts_code"].astype(str)
            daily = daily.set_index(["trade_date", "ts_code"])
            idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
            cand = daily.reindex(idx)
            hit = float(cand["pe_ttm"].notna().mean())
            p("daily_basic 索引名 = %s，行数 %d"
              % (list(daily.index.names), len(daily)))
            p("daily_basic 最大 trade_date = %s"
              % pd.Timestamp(daily.index.get_level_values(0).max()).date())
            p("Top100 F6 est 命中率 = %.0f%%（判定门 50%%）" % (hit * 100))
            if hit >= 0.5:
                est = cand
                est_src = "daily_basic"
    if est is None:
        est_src = "快照 pe_ttm/turn_ma5 兜底（est=None）"
        p("-> est=None，F6 走快照 pe_ttm/turn_ma5 兜底")
    p("F6 取数来源 = %s" % est_src)

    # --- F2/F5 字段映射（复刻生产映射）---
    if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
        pre["main_net"] = pre["mf_main_net"] * 1e4
    if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
        pre["industry_pct"] = pre["ind_pct_ths"]
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pre.columns:
            pre[c] = np.nan
    p("映射后非空率：main_net %.0f%% / industry_pct %.0f%% / volume_ratio %.0f%%"
      % tuple(pre[c].notna().mean() * 100
              for c in ["main_net", "industry_pct", "volume_ratio"]))

    n_filled = G2._fill_f3_events(pre, verbose=True)
    sc = SR.compute_real_scorecard(pre, est)
    pre["total_new"] = sc["total_new_real"].values
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pre["SC_%s" % f] = sc[f].values

    p("Top100 total_new：max=%.1f  中位=%.1f  min=%.1f"
      % (pre["total_new"].max(), pre["total_new"].median(), pre["total_new"].min()))

    # --- 与生产 CSV 逐行逐字段比对 ---
    prod_csv = os.path.join(DC.DATA_DIR, "selections", "g2",
                            "%s_g2_top10.csv" % target.strftime("%Y%m%d"))
    prod = pd.read_csv(prod_csv, encoding="utf-8-sig")
    cols = ["ts_code", "prob", "total_new",
            "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]
    got = pre[pre["total_new"] >= REDLINE].nlargest(10, "total_new")[cols].copy()
    got["prob"] = got["prob"].round(4)
    got["total_new"] = got["total_new"].round(1)

    p("")
    p("独立重算（红线 %.0f -> total_new Top10）：" % REDLINE)
    p(got.to_string(index=False))
    p("")
    p("生产 CSV（%s）：" % os.path.basename(prod_csv))
    p(prod[cols].to_string(index=False))
    p("")

    same_rows = (len(prod) == len(got))
    diffs = []
    if same_rows:
        for c in cols:
            a = prod[c].reset_index(drop=True)
            b = got[c].reset_index(drop=True)
            if a.dtype.kind in "if":
                eq = bool(np.allclose(a.astype(float).values, b.astype(float).values,
                                      rtol=0, atol=1e-9, equal_nan=True))
            else:
                eq = bool((a.astype(str).values == b.astype(str).values).all())
            if not eq:
                diffs.append(c)
    verdict = "完全一致" if (same_rows and not diffs) else "存在差异"
    p("行数一致 = %s（生产 %d / 独立 %d）；字段差异 = %s"
      % (same_rows, len(prod), len(got), diffs if diffs else "无（9/9 全等）"))
    p("```")

    rec("A",
        "OP 独立脚本复现生产 CSV：%s（生产 %d 行 / 独立重算 %d 行，字段差异 %s）"
        % (verdict, len(prod), len(got), diffs if diffs else "无"),
        "live 模型 %s / 快照 %s %d 行 / prob Top%d / F3 面板外补算 %s 行 / F6 来源=%s / 红线 %.0f -> total_new Top10"
        % (os.path.basename(model_path), target.date(), len(day), PRE_POOL,
           n_filled, est_src, REDLINE))

    # --- 命题 B ---
    emit("命题 B：当日 Top100 的 F5 分布与对总分的量化影响")
    p("```")
    ind = pre["industry_pct"].astype(float)
    p("F5 输入列 = industry_pct（生产映射自 %s）"
      % ("ind_pct_ths" if "ind_pct_ths" in pre.columns else "原列"))
    p("industry_pct 原生量纲核验：全池 %d 行取值 %d 个不同值（同行业同值，%d 个行业桶），"
      "原始数值已为百分数（如 -2.71 表示 -2.71%%），故下列不再乘 100"
      % (len(day), day["ind_pct_ths"].nunique(),
         day["ind_pct_ths"].nunique() if "ind_pct_ths" in day.columns else -1))
    p("industry_pct：均值 %.2f%%  中位 %.2f%%  min %.2f%%  max %.2f%%  负值占比 %.1f%%"
      % (ind.mean(), ind.median(), ind.min(), ind.max(),
         (ind < 0).mean() * 100))
    p("")
    dist = pre["SC_F5"].value_counts().sort_index()
    p("SC_F5 分布：")
    for k, v in dist.items():
        p("   F5=%-4s : %3d 只 (%.1f%%)" % (int(k), v, v / len(pre) * 100))
    p("F5 均值 = %.3f（F5 绝对阈值档位（输入已是百分数）：>3→10, >1.5→8, >0→5, >-1→4, else→2）"
      % pre["SC_F5"].mean())
    p("")
    w5 = SR.SC_WEIGHTS["F5"]
    delta = w5 * (5.0 - pre["SC_F5"]) * 10.0
    neu = pre["total_new"] + delta
    p("评分卡对 F5 线性：total 含 W5*F5*10 = %.2f*F5*10 -> F5 中性(5)修正量 = %.2f*(5-F5)"
      % (w5, w5 * 10))
    p("平均压分 = %.2f 分（min %.2f / max %.2f）" % (-delta.mean(), -delta.max(), -delta.min()))
    for ln in THRS:
        p("  >=%.0f : 实际 %2d 只 -> F5 中性化后 %2d 只"
          % (ln, int((pre["total_new"] >= ln).sum()), int((neu >= ln).sum())))
    p("  max : %.1f -> %.1f" % (pre["total_new"].max(), neu.max()))
    p("```")

    rec("B",
        "Top100 industry_pct 均值 %.2f%%（负值占 %.1f%%），F5 均值 %.2f，评分卡平均压分 %.2f 分；"
        "红线 %.0f 命中 %d 只，F5 中性化(全给 5 分)后 %d 只；total_new max %.1f->%.1f"
        % (ind.mean(), (ind < 0).mean() * 100, pre["SC_F5"].mean(), -delta.mean(),
           REDLINE, int((pre["total_new"] >= REDLINE).sum()),
           int((neu >= REDLINE).sum()), pre["total_new"].max(), neu.max()),
        "industry_pct 分位 p10/p50/p90 = %s / %s / %s（%%）；输入为百分数量纲，"
        "F5 阈值 3/1.5/0/-1 与该量纲一致"
        % tuple(round(float(np.nanpercentile(ind, q)), 2) for q in (10, 50, 90)))
    return pre


# ======================================================================
# 面板单次扫描 -> 缓存（命题 C/D/E 的唯一数据源）
# ======================================================================
def _build_maps():
    """复刻 build_per_day 的行情派生：T+1 快照 / ATR14 / MA5 / open_map / 跌停图。"""
    import data_config as DC
    import scan_rotate_cost_real as BT

    t0 = time.time()
    p("[MAPS] 读行情主库 ...")
    daily = pd.read_parquet(
        DC.MAIN_DAILY,
        columns=["open", "high", "low", "close", "up_limit", "down_limit",
                 "vol", "suspend_timing", "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    d0 = pd.Timestamp(BT_START) - pd.Timedelta(days=45)
    daily = daily[daily["trade_date"] >= d0].sort_values(["ts_code", "trade_date"])
    p("[MAPS] 行情 %d 行 / %d 只（%.0fs）"
      % (len(daily), daily["ts_code"].nunique(), time.time() - t0))

    pc = daily.groupby("ts_code")["close"].shift(1)
    tr = pd.concat([daily["high"] - daily["low"],
                    (daily["high"] - pc).abs(),
                    (daily["low"] - pc).abs()], axis=1).max(axis=1)
    daily["_tr"] = tr
    daily["_atr14"] = daily.groupby("ts_code")["_tr"].transform(
        lambda s: s.rolling(14, min_periods=7).mean())
    daily["_atr_pct"] = daily["_atr14"] / daily["close"]
    daily["_ma5"] = daily.groupby("ts_code")["close"].transform(
        lambda s: s.rolling(5, min_periods=3).mean())
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1)
    daily = daily.set_index(["trade_date", "ts_code"])

    maps = {
        "est_frame": daily[["pe_ttm", "turnover_rate", "open_next",
                            "up_limit_next", "vol_next", "suspend_next"]],
        "open_map": daily["open"].to_dict(),
        "down_map": daily["down_limit"].to_dict(),
        "atr_map": daily["_atr_pct"].to_dict(),
        "high_map": daily["high"].to_dict(),
        "ma5_map": daily["_ma5"].to_dict(),
    }
    p("[MAPS] 完成（%.0fs）" % (time.time() - t0))
    return maps


def _load_maps():
    if os.path.exists(MAPS_PKL):
        p("[MAPS] 复用缓存 %s" % os.path.basename(MAPS_PKL))
        with open(MAPS_PKL, "rb") as f:
            return pickle.load(f)
    maps = _build_maps()
    with open(MAPS_PKL, "wb") as f:
        pickle.dump(maps, f, protocol=4)
    return maps


def scan_panel(force=False):
    import lightgbm as lgb
    import pyarrow.parquet as pq
    import scorecard_real as SR

    if (not force) and os.path.exists(CACHE) and os.path.exists(MKT_CACHE):
        p("[CDE] 复用评分缓存 %s" % os.path.basename(CACHE))
        top100 = pd.read_parquet(CACHE)
        mkt = pd.read_parquet(MKT_CACHE)
        meta_p = MKT_CACHE + ".json"
        if os.path.exists(meta_p):
            st = json.load(open(meta_p, encoding="utf-8"))
            STATE["n_short"] = st["n_short"]
            STATE["short_dates"] = st["short_dates"]
        else:
            # 缓存不完整：丢弃重建，避免把未知的 <20 天数当成 0
            p("[CDE] 缓存元数据缺失，强制重建")
            return scan_panel(force=True)
        return top100, mkt

    t0 = time.time()
    avail = set(pq.ParquetFile(PANEL_PATH).schema.names)
    need = set(json.load(open(META_PATH, encoding="utf-8"))["feature_cols"]) | {
        "trade_date", "ts_code", "fwd_ret",
        "pos_250", "volume_ratio", "main_net", "industry_pct", "ind_pct_ths",
        "fc_days_since", "ex_days_since", "fc_pchange", "dv_year_sum",
        "rsi6", "macd_hist", "rel_mom_20", "pe_ttm", "turn_ma5",
    }
    missing = sorted(need - avail)
    if missing:
        raise RuntimeError("面板缺列，无法扫描：%s" % missing)
    cols = sorted(need)

    p("[CDE] 读面板（列下推 %d 列，trade_date in [%s, %s]）..."
      % (len(cols), BT_START, BT_END))
    panel = pd.read_parquet(
        PANEL_PATH, columns=cols,
        filters=[("trade_date", ">=", pd.Timestamp(BT_START)),
                 ("trade_date", "<=", pd.Timestamp(BT_END))])
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    panel["ts_code"] = panel["ts_code"].astype(str)
    p("[CDE] 面板 %d 行 / %d 交易日（%.0fs）"
      % (len(panel), panel["trade_date"].nunique(), time.time() - t0))
    p("[CDE] 面板索引唯一 = %s" % panel.index.is_unique)

    maps = _load_maps()
    est_frame = maps["est_frame"]

    feat_cols = json.load(open(META_PATH, encoding="utf-8"))["feature_cols"]
    booster = lgb.Booster(model_file=MODEL_PATH)

    all_dates = sorted(panel["trade_date"].unique())
    recs, mkt_rows = [], []
    short_dates = []
    for k, d in enumerate(all_dates):
        day = panel[panel["trade_date"] == d].copy()
        # 复刻 build_per_day：<20 行的日期 continue（不进 per_day、不进 market_avgs）
        if len(day) < 20:
            short_dates.append(pd.Timestamp(d))
            continue
        day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
        idx = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"]])
        est = est_frame.reindex(idx)

        sc = SR.compute_real_scorecard(day, est)

        f5_abs = pd.Series(sc["F5"].values, index=day.index)
        f5_rank = rank_f5_of(day["industry_pct"].astype(float)
                             if "industry_pct" in day.columns
                             else day["ind_pct_ths"].astype(float))
        t_abs = pd.Series(sc["total_new_real"].values, index=day.index)
        t_rank = t_abs + SR.SC_WEIGHTS["F5"] * (f5_rank - f5_abs) * 10.0

        mkt_rows.append((pd.Timestamp(d), float(day["fwd_ret"].mean()), len(day)))

        pre = day.nlargest(PRE_POOL, "prob")[["ts_code", "prob", "fwd_ret"]].copy()
        # est 的列是 (trade_date, ts_code) MultiIndex，评分列是 day.index（RangeIndex）。
        # 先把 est 各列按行序取成与 day.index 同索引的 Series，再统一 reindex 到 pre.index。
        est_cols = {}
        for c in ("open_next", "up_limit_next", "vol_next", "suspend_next"):
            est_cols[c] = pd.Series(est[c].to_numpy(), index=day.index)
        for name, ser in (("f5_abs", f5_abs), ("f5_rank", f5_rank),
                          ("t_abs", t_abs), ("t_rank", t_rank),
                          ("open_next", est_cols["open_next"]),
                          ("up_limit_next", est_cols["up_limit_next"]),
                          ("vol_next", est_cols["vol_next"]),
                          ("suspend_next", est_cols["suspend_next"])):
            pre[name] = ser.reindex(pre.index).to_numpy()
        pre["trade_date"] = pd.Timestamp(d)
        recs.append(pre[["trade_date", "ts_code", "prob", "fwd_ret",
                         "f5_abs", "f5_rank", "t_abs", "t_rank",
                         "open_next", "up_limit_next", "vol_next", "suspend_next"]])
        if (k + 1) % 50 == 0:
            p("[CDE]   扫描 %d/%d 日（%.0fs）" % (k + 1, len(all_dates), time.time() - t0))

    top100 = pd.concat(recs, ignore_index=True)
    mkt = pd.DataFrame(mkt_rows, columns=["trade_date", "mkt_avg", "n_rows"])
    top100.to_parquet(CACHE, index=False)
    mkt.to_parquet(MKT_CACHE, index=False)
    STATE["n_short"] = len(short_dates)
    STATE["short_dates"] = [str(x.date()) for x in short_dates]
    with open(MKT_CACHE + ".json", "w", encoding="utf-8") as f:
        json.dump({"n_short": STATE["n_short"],
                   "short_dates": STATE["short_dates"],
                   "n_dates_all": len(all_dates),
                   "n_dates_used": len(mkt)}, f)
    p("[CDE] 扫描完成：dates(面板) %d 日，其中 <20 行跳过 %d 日，实际打分 %d 日；"
      "Top%d 明细 %d 行（%.0fs）"
      % (len(all_dates), STATE["n_short"], len(mkt), PRE_POOL, len(top100),
         time.time() - t0))
    return top100, mkt


# ======================================================================
# 命题 C / D / E
# ======================================================================
def run_cde(top100, mkt):
    import scorecard_real as SR

    dates = [pd.Timestamp(x) for x in sorted(top100["trade_date"].unique())]
    is_dates = [d for d in dates if d < IS_CUT]
    oos_dates = [d for d in dates if d >= IS_CUT]

    # ---------------- 命题 C ----------------
    emit("命题 C：%s ~ %s 红线通过率（生产 Top%d 口径）" % (BT_START, BT_END, PRE_POOL))
    p("```")
    p("口径：逐日 LightGBM predict -> 逐日 compute_real_scorecard -> 概率 Top%d 预选池 -> 统计 total_new>=红线 只数"
      % PRE_POOL)
    p("打分 universe = 当日面板全池（compute_real_scorecard 为逐行 apply，与 Top100 打分逐行等价）")
    p("样本：面板区间 %d 个交易日，其中 <20 行被跳过 %d 日，实际统计 %d 日"
      % (len(dates) + (STATE["n_short"] or 0), STATE["n_short"] or 0, len(dates)))
    p("")
    cnt = {}
    p("| 红线 | 日均通过数 | 中位 | 有候选天数 | >=10 只天数 | 空池天数 | 空池日占比 |")
    p("|---:|---:|---:|---:|---:|---:|---:|")
    for thr in THRS:
        c = (top100[top100["t_abs"] >= thr].groupby("trade_date").size()
             .reindex(pd.Index(dates)).fillna(0).astype(int))
        cnt[thr] = c
        p("| %.0f | %.2f | %.1f | %d/%d | %d | %d | %.1f%% |"
          % (thr, c.mean(), c.median(), int((c > 0).sum()), len(dates),
             int((c >= 10).sum()), int((c == 0).sum()), (c == 0).mean() * 100))
    p("")
    c60 = cnt[REDLINE]
    empty60 = [str(d.date()) for d in dates if c60.loc[d] == 0]
    p("红线 %.0f 过线数分布：min=%d  p25=%.0f  p50=%.0f  p75=%.0f  max=%d"
      % (REDLINE, c60.min(), c60.quantile(.25), c60.quantile(.5),
         c60.quantile(.75), c60.max()))
    p("红线 %.0f 空池日（%d 天）：%s"
      % (REDLINE, len(empty60), empty60 if empty60 else "无"))
    p("")
    p("各红线空池天数：%s"
      % "  ".join("%.0f→%d 天" % (t, int((cnt[t] == 0).sum())) for t in THRS))
    p("```")
    rec("C",
        "生产 Top%d 口径下红线 %.0f：日均通过 %.2f 只 / 中位 %.0f，空池 %d 天（%.1f%%），"
        ">=10 只 %d 天（%.1f%%）；红线 55/52/50 日均分别 %.2f / %.2f / %.2f 只，空池分别 %d / %d / %d 天"
        % (PRE_POOL, REDLINE, c60.mean(), c60.median(), int((c60 == 0).sum()),
           (c60 == 0).mean() * 100, int((c60 >= 10).sum()), (c60 >= 10).mean() * 100,
           cnt[55.0].mean(), cnt[52.0].mean(), cnt[50.0].mean(),
           int((cnt[55.0] == 0).sum()), int((cnt[52.0] == 0).sum()),
           int((cnt[50.0] == 0).sum())),
        "%d 个交易日逐日 predict+打分（panel=%s, model=%s）"
        % (len(dates), os.path.basename(PANEL_PATH), os.path.basename(MODEL_PATH)))

    # ---------------- 命题 D ----------------
    emit("命题 D：绝对阈值 F5 vs rank-F5 的 IS/OOS 超额对比")
    p("```")
    p("回测买入口径（scan_rotate_cost_real.simulate）：概率 Top%d -> 红线 %.0f -> total_new 降序，"
      "持仓 %d 只，N=%d，滑点 %.1f%%，可执行 open->open（一字板/停牌/无量过滤）"
      % (TOP10, REDLINE, TOP_HOLD, N_HOLD, SLIP * 100))
    p("IS/OOS 分界 = %s（IS %d 日 / OOS %d 日）"
      % (IS_CUT.date(), len(is_dates), len(oos_dates)))
    p("两组唯一差异 = F5 分量：t_rank = t_abs + W5*(F5rank - F5abs)*10，其余分量/红线/成本/池完全相同")
    p("F5rank 构造口径 = 当日 universe 内 industry_pct 百分位分 5 档（非生产口径，仅作对照）")
    p("")

    import scan_rotate_cost_real as BT
    maps = _load_maps()
    BT.THRESHOLD = REDLINE
    BT.TOP = TOP_HOLD
    BT._DOWN_LIMIT_MAP = maps["down_map"]
    BT._ATR_MAP = maps["atr_map"]
    BT._HIGH_MAP = maps["high_map"]
    BT._MA5_MAP = maps["ma5_map"]
    open_map = maps["open_map"]

    per_day = {}
    for d in dates:
        per_day[d] = (top100[top100["trade_date"] == d]
                      .nlargest(TOP10, "prob")
                      .set_index("ts_code")[
                          ["t_abs", "t_rank", "fwd_ret", "prob",
                           "open_next", "up_limit_next", "vol_next", "suspend_next"]])
    p("per_day 复刻校验：每日行数 min=%d max=%d（期望恒为 %d）"
      % (min(len(v) for v in per_day.values()),
         max(len(v) for v in per_day.values()), TOP10))

    # 两种 F5 口径各自预先重命名为 simulate 需要的 total_new，
    # 避免 sim() 二次转换时把 t_abs 当成唯一真相（rank 分支会被污染）
    per_day_abs = {}
    per_day_rank = {}
    for d, r in per_day.items():
        per_day_abs[d] = r.rename(columns={"t_abs": "total_new"})
        per_day_rank[d] = r.rename(columns={"t_rank": "total_new"})
    p("重命名校验：abs 字典 total_new 列 = t_abs（%s），rank 字典 total_new 列 = t_rank（%s）"
      % (bool(np.allclose(per_day_abs[dates[0]]["total_new"].values,
                          per_day[dates[0]]["t_abs"].values, equal_nan=True)),
         bool(np.allclose(per_day_rank[dates[0]]["total_new"].values,
                          per_day[dates[0]]["t_rank"].values, equal_nan=True))))

    mkt_by_date = pd.Series(mkt["mkt_avg"].values,
                            index=pd.DatetimeIndex(mkt["trade_date"]))

    def maxdd(r):
        if len(r) == 0:
            return float("nan")
        c = np.cumprod(1 + np.asarray(r, dtype=float))
        return float(np.min(c / np.maximum.accumulate(c) - 1))

    def sim(pdx, dts):
        # pdx 已预先重命名好 total_new（abs / rank 各一份），此处不再做任何列转换
        assert "total_new" in list(list(pdx.values())[0].columns), "pdx 缺 total_new"
        tr, dr, ns = BT.simulate(dts, pdx, N_HOLD, SLIP, open_map, exec_ok=True)
        return tr, np.asarray(dr, dtype=float), ns

    rows = []
    for mode in ("abs", "rank"):
        pdx = per_day_abs if mode == "abs" else per_day_rank
        for seg, dts in (("IS", is_dates), ("OOS", oos_dates), ("FULL", dates)):
            tr, dr, ns = sim(pdx, dts)
            mk = mkt_by_date.reindex(pd.DatetimeIndex(dts)).values.astype(float)
            ex = dr - mk
            rows.append([mode, seg, float(np.nanmean(dr)), float(np.nanmean(ex)),
                         maxdd(dr), len(tr), ns])
    p("| F5 版本 | 区间 | 策略日均收益 | 日均超额(日期对齐) | 策略最大回撤 | 交易笔数 | 可执行跳过 |")
    p("|---|---|---:|---:|---:|---:|---:|")
    for r in rows:
        p("| %s-F5 | %s | %+.3f%% | %+.3f%% | %.1f%% | %d | %d |"
          % (r[0], r[1], r[2] * 100, r[3] * 100, r[4] * 100, r[5], r[6]))
    p("")

    fa = top100["f5_abs"]
    fr = top100["f5_rank"]
    p("F5 分量统计（Top%d 预选池 × %d 日 = %d 样本）：" % (PRE_POOL, len(dates), len(top100)))
    p("  绝对阈值 F5：均值 %.3f，标准差 %.3f，取值分布 %s"
      % (fa.mean(), fa.std(), dict(sorted(fa.value_counts().items()))))
    p("  rank-F5    ：均值 %.3f，标准差 %.3f" % (fr.mean(), fr.std()))
    p("  绝对 F5 单档化(>=90%% 同值)日占比 = %.1f%%"
      % (top100.groupby("trade_date")["f5_abs"]
         .apply(lambda s: (s == s.mode().iloc[0]).mean() > 0.9).mean() * 100))
    p("  F5 权重 %.2f -> F5 满档差 8 分 = %.1f 分" % (SR.SC_WEIGHTS["F5"],
                                                     SR.SC_WEIGHTS["F5"] * 8 * 10))
    p("```")
    d = {(r[0], r[1]): r[3] for r in rows}
    rec("D",
        "红线 %.0f / 持仓 %d / N=%d / 滑点 %.1f%% / 可执行口径：abs-F5 日均超额 IS %+.3f%%、OOS %+.3f%%、FULL %+.3f%%；"
        "rank-F5 日均超额 IS %+.3f%%、OOS %+.3f%%、FULL %+.3f%%；OOS 差 %+.3f pp，FULL 差 %+.3f pp。"
        "Top%d 池内 abs-F5 均值 %.2f(标准差 %.2f) vs rank-F5 均值 %.2f(标准差 %.2f)"
        % (REDLINE, TOP_HOLD, N_HOLD, SLIP * 100,
           d[("abs", "IS")] * 100, d[("abs", "OOS")] * 100, d[("abs", "FULL")] * 100,
           d[("rank", "IS")] * 100, d[("rank", "OOS")] * 100, d[("rank", "FULL")] * 100,
           (d[("abs", "OOS")] - d[("rank", "OOS")]) * 100,
           (d[("abs", "FULL")] - d[("rank", "FULL")]) * 100,
           PRE_POOL, fa.mean(), fa.std(), fr.mean(), fr.std()),
        "同 panel/模型/成本/红线/候选池/IS-OOS 分界，仅替换 F5 分量；市场基准按 trade_date join")

    # ---------------- 命题 E ----------------
    emit("命题 E：audit_stats_walkforward.align_market 对齐方式核验")
    p("```")
    import inspect
    import faulthandler
    import audit_stats_walkforward as AW
    # 撤销其 import 时的 600s 自杀定时器（老版本 Python 无此函数，用 hasattr 兜底）
    if hasattr(faulthandler, "cancel_dump_traceback_later"):
        faulthandler.cancel_dump_traceback_later()
    else:
        p("[E] 当前 Python 无 faulthandler.cancel_dump_traceback_later，"
          "600s 定时器保持挂起（不影响本段计算）")
    src = inspect.getsource(AW.align_market)
    p("当前实现源码（inspect.getsource 原文）：")
    for ln in src.strip().splitlines():
        p("    " + ln)
    p("")
    p("调用链：main() -> is_dates/oos_dates 为 dates 的真前缀/真后缀 -> run_excess -> "
      "align_market(dates, mkt_full) -> ex = daily_ret - mkt")
    p("mkt_full = build_per_day 的 pd.Series(market_avgs)，仅对通过 len(day)>=20 的日期 append")
    p("")
    n_used = len(mkt)
    mkt_full = pd.Series(mkt["mkt_avg"].values)   # 位置序列，模拟 build_per_day 返回
    p("本区间：面板 dates %d 日，<20 行跳过 %d 日，mkt_full 长度 %d -> 长度%s"
      % (n_used + (STATE["n_short"] or 0), STATE["n_short"] or 0, len(mkt_full),
         "一致" if len(mkt_full) == n_used else "不一致"))

    def cmp_seg(name, dts):
        pos = AW.align_market(dts, mkt_full)
        cor = mkt_by_date.reindex(pd.DatetimeIndex(dts)).values.astype(float)
        bad = ~np.isclose(pos, cor, equal_nan=True)
        p("")
        p("%s 段（%d 日）位置对齐 vs 日期对齐：" % (name, len(dts)))
        p("  前 3 日 位置 = %s" % np.round(pos[:3] * 100, 4).tolist())
        p("  前 3 日 日期 = %s" % np.round(cor[:3] * 100, 4).tolist())
        p("  完全相等 = %s；错配日数 = %d/%d（%.1f%%）；最大绝对差 = %.4f pp"
          % (bool(np.allclose(pos, cor, equal_nan=True)), int(bad.sum()), len(dts),
             bad.mean() * 100, float(np.nanmax(np.abs(pos - cor))) * 100))
        return pos, cor, int(bad.sum())

    pos_is, cor_is, bad_is = cmp_seg("IS", is_dates)
    pos_oos, cor_oos, bad_oos = cmp_seg("OOS", oos_dates)
    pos_full, cor_full, bad_full = cmp_seg("FULL", dates)

    p("")
    _, dr_is, _ = sim(per_day_abs, is_dates)
    _, dr_oos, _ = sim(per_day_abs, oos_dates)
    _, dr_full, _ = sim(per_day_abs, dates)
    p("日均超额口径影响（策略同为 abs-F5，仅基准对齐方式不同）：")
    p("  IS   位置 %+.3f%%  |  日期 %+.3f%%  |  偏移 %+.4f pp"
      % (np.nanmean(dr_is - pos_is) * 100, np.nanmean(dr_is - cor_is) * 100,
         (np.nanmean(dr_is - pos_is) - np.nanmean(dr_is - cor_is)) * 100))
    p("  OOS  位置 %+.3f%%  |  日期 %+.3f%%  |  偏移 %+.4f pp"
      % (np.nanmean(dr_oos - pos_oos) * 100, np.nanmean(dr_oos - cor_oos) * 100,
         (np.nanmean(dr_oos - pos_oos) - np.nanmean(dr_oos - cor_oos)) * 100))
    p("  FULL 位置 %+.3f%%  |  日期 %+.3f%%  |  偏移 %+.4f pp"
      % (np.nanmean(dr_full - pos_full) * 100, np.nanmean(dr_full - cor_full) * 100,
         (np.nanmean(dr_full - pos_full) - np.nanmean(dr_full - cor_full)) * 100))
    p("")
    p("附带核验：若存在 <20 行日期，dates 会保留该日但 market_avgs 不 append，")
    p("mkt_full 与 dates 长度不一致会使位置对齐彻底失效；本次实测 <20 行日期数 = %d。"
      % (STATE["n_short"] or 0))
    p("```")
    rec("E",
        "align_market 按位置 i 取 mkt_full.iloc[i]、不使用 trade_date 查询：IS 段（%d 日）错配 %d 日，"
        "OOS 段（%d 日）错配 %d 日（%.1f%%，最大绝对差 %.4f pp），FULL 段（%d 日）错配 %d 日；"
        "IS 日均超额偏移 %+.4f pp，OOS 偏移 %+.4f pp（位置 %+.3f%% -> 日期对齐 %+.3f%%）"
        % (len(is_dates), bad_is, len(oos_dates), bad_oos,
           bad_oos / max(1, len(oos_dates)) * 100,
           float(np.nanmax(np.abs(pos_oos - cor_oos))) * 100,
           len(dates), bad_full,
           (np.nanmean(dr_is - pos_is) - np.nanmean(dr_is - cor_is)) * 100,
           (np.nanmean(dr_oos - pos_oos) - np.nanmean(dr_oos - cor_oos)) * 100,
           np.nanmean(dr_oos - pos_oos) * 100, np.nanmean(dr_oos - cor_oos) * 100),
        "inspect.getsource 原文核验 + IS/OOS/FULL 三段逐日位置对齐 vs 日期对齐比对 + 同一策略日收益序列相减")


def run_c(top100, mkt):
    """C 单独入口（调试用）。"""
    import scorecard_real as SR
    dates = [pd.Timestamp(x) for x in sorted(top100["trade_date"].unique())]
    cnt = {}
    for thr in THRS:
        cnt[thr] = (top100[top100["t_abs"] >= thr].groupby("trade_date").size()
                    .reindex(pd.Index(dates)).fillna(0).astype(int))
    for thr in THRS:
        c = cnt[thr]
        p("红线 %.0f：日均 %.2f 中位 %.0f 有候选 %d/%d >=10只 %d 空池 %d"
          % (thr, c.mean(), c.median(), int((c > 0).sum()), len(dates),
             int((c >= 10).sum()), int((c == 0).sum())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="all", choices=["all", "ab", "c", "cde"])
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    head = [
        "# XVAL OP 独立交叉验证报告（2026-09-28 T-20260928-003）",
        "",
        "> 生成：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "> 独立性声明：本报告未参考 Deveco 的任何脚本与结论，全部由 OP 从生产源码与原始数据独立推导。",
        "> 只读：未修改任何生产代码，未写入 data_live / data/selections / D:/QMT_POOL。",
        "> 口径常量读自源码：面板 %s / 模型 %s / 区间 %s~%s / 预选池 %d / 红线 %.0f / "
        "IS-OOS 分界 %s / N=%d / 持仓 %d / 滑点 %.1f%%"
        % (os.path.basename(PANEL_PATH), os.path.basename(MODEL_PATH), BT_START, BT_END,
           PRE_POOL, REDLINE, IS_CUT.date(), N_HOLD, TOP_HOLD, SLIP * 100),
        "",
    ]
    LINES.extend(head)

    os.environ["BT_PANEL"] = PANEL_PATH
    os.environ["BT_MODEL"] = MODEL_PATH
    os.environ["BT_META"] = META_PATH
    os.environ["BT_THRESHOLD"] = str(REDLINE)
    os.environ["BT_TOP"] = str(TOP_HOLD)
    os.environ["BT_START"] = BT_START
    os.environ["BT_END"] = BT_END

    if args.part in ("all", "ab"):
        run_live()
    if args.part == "c":
        top100, mkt = scan_panel(force=args.force)
        run_c(top100, mkt)
    if args.part in ("all", "cde"):
        emit("数据准备：回测面板单次扫描（命题 C/D/E 唯一数据源）")
        top100, mkt = scan_panel(force=args.force)
        run_cde(top100, mkt)

    LINES.append("")
    LINES.append("## 汇总表")
    LINES.append("")
    LINES.append("| 命题 | OP 结论 |")
    LINES.append("|---|---|")
    for t, c, _ in RESULTS:
        LINES.append("| %s | %s |" % (t, c.replace("|", "/")))

    out = os.path.join(PROJ, "results", "XVAL_OP_20260928.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(LINES))
    print("\n[OK] 报告:", out, flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        out = os.path.join(PROJ, "results", "XVAL_OP_20260928.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(LINES))
        print("[ERR] 部分完成，已落盘:", out, flush=True)
        raise
