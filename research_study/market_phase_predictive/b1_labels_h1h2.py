# -*- coding: utf-8 -*-
"""
B 路线 Part 1: IS-only 标定 + 双口径标签回填 + H1/H2 冻结判据
=============================================================
预注册依据: research_study/market_phase_backfill/预注册方案_B路线预测力检验.md
(2026-09-28 诚哥拍板执行; 本脚本只实现冻结判据, 跑后不改任何门限)

冻结口径:
  IS 标定窗口 : 2020-08-24 ~ 2023-12-31 (TSP 同款分位数配方, 与生产标定同舍入)
  OOS         : 2024-01-01 ~ 2026-09-11
  热度综合分  : height/ge2/promo_rate 的 EMA(alpha=1/3) 各自滚动 250 日分位
                (min_periods=120, 含当日, PIT) 等权平均 -> heat_score;
                再滚动 250 日分位 -> heat_pct (top decile = heat_pct>=0.90)
  小盘池      : circ_mv<50亿 & is_st!=1 & listed_days>=60 (P17 v1/v2/v3 同口径)
  收益口径    : r=log1p(pct_chg/100); fwdK = sum(r[T+1..T+K]);
                base20(T) = 池内逐股 fwd20 对数均值 (P17 v3 基线同构);
                minpath20(T) = min_k cum(pool 日均对数收益 T+1..T+k) (回撤 proxy)
  H1 (描述)   : spearman(heat_score, base20), IS/OOS 分报, 无 pass/fail
  H2 (主判据1): OOS 内 heat_pct>=0.90 组 vs 其余, 非重叠抽样(间隔>=20 交易日位),
                单边 Welch t (方向: top 组 minpath20 更深) ;
                门限 p<0.05 且 (mean_rest - mean_top)>=0.01 (1pp) -> H2 通过
标签:
  phase_is   : IS-only 标定阈值 (主口径, 防标签前视)
  phase_prod : 生产全窗口标定阈值 (对照列, 与 factors/market_phase.py 常量一致)
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, r"D:\QuantLab")
import factors.market_phase as mp  # noqa: E402

ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
HS300_ETF = r"D:\QuantLab\data\etf_daily\510300_SH.csv"
LIVE_CSV = "D:/QMT_POOL/market_phase.csv"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- 冻结参数(跑后禁改) ----
CAL_START = "2020-08-24"
CAL_END = "2023-12-31"
OOS_START = "2024-01-01"
OOS_END = "2026-09-11"
RANK_WIN = 250
RANK_MIN = 120
TOP_DECILE = 0.90
H2_P = 0.05
H2_DIFF = 0.01           # 1pp (对数口径近似)
SAMPLE_GAP = 20
PATCH_DATE = "2026-07-27"  # astock up_limit 单日空洞, 从 live CSV 补

THRESHOLD_NAMES = [
    "CLIMAX_GE2", "CLIMAX_FIRST_BOARD",
    "RALLY_HEIGHT", "RALLY_GE2", "RALLY_PROMO",
    "RALLY_PROMO_ALT", "RALLY_GE2_ALT", "RALLY_HEIGHT_ALT",
    "EBB_PROMO", "EBB_PROMO_STRICT", "EBB_SEAL",
    "EBB_RECENT_GE2", "EBB_RECENT_HEIGHT",
    "IGNITE_GE2", "IGNITE_PROMO", "IGNITE_HEIGHT", "IGNITE_PROMO_SOFT",
    "ICE_HEIGHT", "ICE_GE2", "ICE_FIRST_BOARD",
]


def log(msg):
    print(msg, flush=True)


def calibrate_thresholds(rep):
    """TSP 同款分位数配方(与 backfill_market_phase.calibrate 同舍入), 返回 dict。"""
    q = rep[["height", "first_board", "ge2", "promo_rate", "seal_rate"]].quantile(
        [0.10, 0.20, 0.30, 0.40, 0.50, 0.55, 0.60, 0.85, 0.90])
    h, fb, g2, pr, sr = (q[c] for c in ["height", "first_board", "ge2", "promo_rate", "seal_rate"])
    r3 = lambda v: round(float(v), 3)
    return {
        "CLIMAX_GE2": int(round(2 * g2[0.90])),
        "CLIMAX_FIRST_BOARD": int(round(2.5 * fb[0.90])),
        "RALLY_HEIGHT": int(round(h[0.60])),
        "RALLY_GE2": int(round(g2[0.60])),
        "RALLY_PROMO": r3(pr[0.60]),
        "RALLY_PROMO_ALT": r3(pr[0.85]),
        "RALLY_GE2_ALT": int(round(0.8 * g2[0.60])),
        "RALLY_HEIGHT_ALT": int(round(5.0 / 7.0 * h[0.60])),
        "EBB_PROMO": r3(pr[0.20]),
        "EBB_PROMO_STRICT": r3(pr[0.20] * 0.867),
        "EBB_SEAL": r3(sr[0.10]),
        "EBB_RECENT_GE2": int(round(0.48 * g2[0.90])),
        "EBB_RECENT_HEIGHT": int(round(0.857 * h[0.60])),
        "IGNITE_GE2": int(round(g2[0.30])),
        "IGNITE_PROMO": r3(pr[0.55]),
        "IGNITE_HEIGHT": int(round(h[0.40])),
        "IGNITE_PROMO_SOFT": r3(pr[0.50]),
        "ICE_HEIGHT": int(round(h[0.10])),
        "ICE_GE2": int(round(g2[0.10])),
        "ICE_FIRST_BOARD": int(round(fb[0.10])),
    }


def apply_thresholds(d):
    orig = {n: getattr(mp, n) for n in THRESHOLD_NAMES}
    for n, v in d.items():
        setattr(mp, n, v)
    return orig


def restore_thresholds(orig):
    for n, v in orig.items():
        setattr(mp, n, v)


def rolling_pct(s):
    """当前值在过去 RANK_WIN 日(含当日)中的分位, PIT。"""
    try:
        return s.rolling(RANK_WIN, min_periods=RANK_MIN).rank(pct=True)
    except Exception:
        return s.rolling(RANK_WIN, min_periods=RANK_MIN).apply(
            lambda w: float((w <= w[-1]).mean()), raw=True)


def nonoverlap(s, cal_pos, gap):
    """贪心非重叠抽样(P17 v3 同款): 相邻入选点间隔>=gap 个交易日位置。"""
    picked, last = [], -10 ** 9
    for d, v in s.sort_index().items():
        if pd.isna(v):
            continue
        p = cal_pos[d]
        if p - last >= gap:
            picked.append(float(v))
            last = p
    return np.asarray(picked, dtype=float)


def main():
    t0 = time.time()
    res = {}

    # ---------- 1. 读日线 ----------
    log("[1] 读 astock 日线 ...")
    df = pd.read_parquet(ASTOCK_DAILY, columns=[
        "high", "close", "pre_close", "up_limit", "adj_factor",
        "pct_chg", "circ_mv", "is_st", "listed_days"])
    df = df.reset_index()
    df["ts_code"] = df["ts_code"].astype("category")
    log("    rows=%d, %s ~ %s" % (len(df), df["trade_date"].min().date(),
                                  df["trade_date"].max().date()))

    # ---------- 2. 梯队指标(与 backfill 逐行一致) ----------
    log("[2] 涨停标志 + 连板 + above_ma20 ...")
    df["change_pct"] = df["close"] / df["pre_close"] - 1.0
    df = df.sort_values(["ts_code", "trade_date"], kind="mergesort")
    adj_close = df["close"] * df["adj_factor"]
    ma20 = adj_close.groupby(df["ts_code"], observed=True).transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    df["above_ma20"] = (adj_close > ma20).astype(bool)
    df = df.drop(columns=["pre_close", "adj_factor"])
    flagged = mp.add_limit_flags(df)
    log("    flagged rows=%d, took %.0fs" % (len(flagged), time.time() - t0))

    etf = pd.read_csv(HS300_ETF)
    etf["date"] = pd.to_datetime(etf["date"])
    etf = etf.sort_values("date")
    idx_pct = dict(zip(etf["date"], np.nan_to_num(etf["close"].pct_change().values)))

    daily = mp.build_daily_metrics(flagged, index_pct=idx_pct)

    # ---------- 3. 补 2026-07-27 空洞日 ----------
    if pd.Timestamp(PATCH_DATE) not in daily.index:
        try:
            live = pd.read_csv(LIVE_CSV)
            live["trade_date"] = pd.to_datetime(live["trade_date"])
            row = live[live["trade_date"] == pd.Timestamp(PATCH_DATE)]
            if len(row):
                row = row.set_index("trade_date")
                for c in daily.columns:
                    if c in row.columns:
                        daily.loc[pd.Timestamp(PATCH_DATE), c] = row.iloc[0][c]
                daily = daily.sort_index()
                log("[3] %s 空洞已从 live CSV 补齐" % PATCH_DATE)
        except Exception as e:
            log("[3] %s 补齐失败(忽略): %s" % (PATCH_DATE, e))
    else:
        log("[3] %s 主库已有, 无需补" % PATCH_DATE)

    # ---------- 4. IS-only 标定 ----------
    rep_is = daily.loc[CAL_START:CAL_END]
    thr = calibrate_thresholds(rep_is)
    log("[4] IS-only 标定(%s ~ %s, %d 日):" % (CAL_START, CAL_END, len(rep_is)))
    for k, v in thr.items():
        log("    %-20s = %s (生产 %s)" % (k, v, getattr(mp, k)))
    res["thresholds_is"] = thr

    # ---------- 5. 双口径标签 ----------
    log("[5] 双口径标签 ...")
    states, scores = mp.classify_state_series(daily)
    daily["state"] = states
    daily["score"] = scores
    orig = apply_thresholds(thr)
    daily = mp.classify_phase_series(daily, states=states)
    daily = daily.rename(columns={"phase": "phase_is"})
    restore_thresholds(orig)
    daily = mp.classify_phase_series(daily, states=states)
    daily = daily.rename(columns={"phase": "phase_prod"})
    log("    phase_is   分布(2020-08-24+): %s" %
        daily.loc[CAL_START:, "phase_is"].value_counts(normalize=True).round(4).to_dict())
    log("    phase_prod 分布(2020-08-24+): %s" %
        daily.loc[CAL_START:, "phase_prod"].value_counts(normalize=True).round(4).to_dict())

    # ---------- 6. 热度综合分 ----------
    log("[6] 热度综合分 ...")
    ema = {
        "height": mp.ema_ffill([float(v) if pd.notna(v) else None for v in daily["height"]]),
        "ge2": mp.ema_ffill([float(v) if pd.notna(v) else None for v in daily["ge2"]]),
        "promo": mp.ema_ffill([float(v) if pd.notna(v) else None for v in daily["promo_rate"]]),
    }
    daily["heat_score"] = sum(rolling_pct(pd.Series(v, index=daily.index))
                              for v in ema.values()) / 3.0
    daily["heat_pct"] = rolling_pct(daily["heat_score"])

    # ---------- 7. 小盘池 fwd / minpath ----------
    log("[7] 小盘池 fwd 与 minpath ...")
    pool = df[(df["circ_mv"] < 500000) & (df["is_st"] != 1)
              & (df["listed_days"].fillna(1e9) >= 60)]
    r = np.log1p(pool["pct_chg"].astype(float) / 100.0)
    pr = r.groupby(pool["trade_date"]).mean()          # 池日均对数收益
    pr = pr.reindex(daily.index)
    # 池内逐股 fwd20 对数均值 (P17 v3 基线同构)
    fwd_tab = pool[["trade_date", "ts_code"]].copy()
    fwd_tab["r"] = r.values
    wide_r = fwd_tab.pivot_table(index="trade_date", columns="ts_code", values="r", aggfunc="first")
    del fwd_tab
    fw20 = sum(wide_r.shift(-k) for k in range(1, 21))
    daily["base20"] = fw20.mean(axis=1).reindex(daily.index)
    del wide_r, fw20
    # minpath20: cs 数组滑窗
    cs = np.cumsum(np.nan_to_num(pr.values))
    n = len(cs)
    minpath = np.full(n, np.nan)
    for i in range(n - 20):
        minpath[i] = cs[i + 1:i + 21].min() - cs[i]
    daily["minpath20"] = minpath
    log("    池日均收益覆盖 %s ~ %s" % (pr.first_valid_index(), pr.last_valid_index()))

    daily.index.name = "trade_date"
    out_csv = os.path.join(OUT_DIR, "daily_phase_b_route.csv")
    daily.to_csv(out_csv, encoding="utf-8-sig")
    log("    已保存 %s (rows=%d, took %.0fs)" % (out_csv, len(daily), time.time() - t0))

    # ---------- 8. H1 (描述性) ----------
    log("")
    log("========== H1 复现性确认(描述性, 无 pass/fail) ==========")
    oos = daily.loc[OOS_START:OOS_END]
    isw = daily.loc[CAL_START:CAL_END]
    h1 = {}
    for name, seg in (("IS", isw), ("OOS", oos), ("FULL2020+", daily.loc[CAL_START:])):
        m = seg["heat_score"].notna() & seg["base20"].notna()
        if m.sum() < 30:
            continue
        ic = stats.spearmanr(seg.loc[m, "heat_score"], seg.loc[m, "base20"]).correlation
        h1[name] = float(ic)
        log("  H1 Spearman(heat, base20) %s: %+.4f (n=%d)" % (name, ic, m.sum()))
    res["h1"] = h1
    # 分阶段 base20 均值(描述)
    for col in ("phase_is", "phase_prod"):
        tab = oos.groupby(col)["base20"].agg(["count", "mean"])
        log("  OOS 分阶段 base20(%s):" % col)
        for ph, row in tab.iterrows():
            log("    %-8s n=%-4d mean=%+.4f" % (ph, int(row["count"]), row["mean"]))
        res["h1_stage_base20_%s" % col] = {str(k): float(v) for k, v in tab["mean"].items()}

    # ---------- 9. H2 (主判据 1) ----------
    log("")
    log("========== H2 尾部风险预测(主判据 1) ==========")
    cal_pos = pd.Series(np.arange(len(daily.index)), index=daily.index)
    m = oos["heat_pct"].notna() & oos["minpath20"].notna()
    top = oos.loc[m & (oos["heat_pct"] >= TOP_DECILE), "minpath20"]
    rest = oos.loc[m & (oos["heat_pct"] < TOP_DECILE), "minpath20"]
    x_top = nonoverlap(top, cal_pos, SAMPLE_GAP)
    x_rest = nonoverlap(rest, cal_pos, SAMPLE_GAP)
    t2, p2 = stats.ttest_ind(x_top, x_rest, alternative="less", equal_var=False)
    diff = float(x_rest.mean() - x_top.mean())
    h2_pass = bool(p2 < H2_P and diff >= H2_DIFF)
    log("  OOS top decile 日数=%d(非重叠 %d 点) vs 其余=%d(非重叠 %d 点)" % (
        len(top), len(x_top), len(rest), len(x_rest)))
    log("  mean(minpath|top)=%+.4f ≈ %+.2f%% | mean(minpath|rest)=%+.4f ≈ %+.2f%%" % (
        x_top.mean(), (np.expm1(x_top.mean())) * 100, x_rest.mean(), (np.expm1(x_rest.mean())) * 100))
    log("  差(更深为正)=%+.4f ≈ %.2fpp | Welch 单边 t=%.3f p=%.4f" % (diff, diff * 100, t2, p2))
    log("  冻结门限: p<%.2f 且 差>=%.2f -> %s" % (H2_P, H2_DIFF, "PASS" if h2_pass else "FAIL"))
    # CVaR5% 描述
    cvar = {}
    for name, arr in (("top", top.dropna().values), ("rest", rest.dropna().values)):
        if len(arr):
            k = max(1, int(len(arr) * 0.05))
            cvar[name] = float(np.sort(arr)[:k].mean())
            log("  CVaR5%%(%s) = %+.4f ≈ %+.2f%%" % (name, cvar[name], np.expm1(cvar[name]) * 100))
    res["h2"] = {
        "n_top_days": int(len(top)), "n_rest_days": int(len(rest)),
        "n_top_nolap": int(len(x_top)), "n_rest_nolap": int(len(x_rest)),
        "mean_top": float(x_top.mean()), "mean_rest": float(x_rest.mean()),
        "diff": diff, "t": float(t2), "p": float(p2), "pass": h2_pass,
    }

    # ---------- 10. OOS climax/ebb 段事件描述 + 2024-01 假阴性 ----------
    log("")
    log("========== 段级事件描述(非判据) ==========")
    seg_rows = []
    for col in ("phase_is", "phase_prod"):
        seg = daily.loc[OOS_START:OOS_END, col]
        lab = seg.values
        edges = np.flatnonzero(lab[1:] != lab[:-1])
        starts = np.r_[0, edges + 1]
        ends = np.r_[edges, len(lab) - 1]
        for s, e in zip(starts, ends):
            ph = lab[s]
            if ph not in ("climax", "ebb"):
                continue
            d0, d1 = seg.index[s], seg.index[e]
            if d1 in daily.index and pd.notna(daily.loc[d1, "minpath20"]):
                mp_v, b20 = float(daily.loc[d1, "minpath20"]), float(daily.loc[d1, "base20"])
            else:
                mp_v, b20 = np.nan, np.nan
            seg_rows.append({"caliber": col, "phase": ph, "start": str(d0.date()),
                             "end": str(d1.date()), "len": int(e - s + 1),
                             "minpath20_at_end": mp_v, "base20_at_end": b20})
            log("  [%s] %-7s %s ~ %s (%d 日) 段末 minpath20=%s base20=%s" % (
                col, ph, d0.date(), d1.date(), e - s + 1,
                ("%+.2f%%" % (np.expm1(mp_v) * 100)) if pd.notna(mp_v) else "NA",
                ("%+.2f%%" % (np.expm1(b20) * 100)) if pd.notna(b20) else "NA"))
    res["segments_oos"] = seg_rows
    # 2024-01 假阴性: 微盘崩窗口的 phase 与池路径
    w = daily.loc["2024-01-02":"2024-02-08"]
    if len(w):
        worst = w["minpath20"].min()
        worst_d = w["minpath20"].idxmin()
        log("  [假阴性检查] 2024-01-02~02-08 phase_is 序列: %s" %
            "->".join(dict.fromkeys(w["phase_is"].values)))
        log("    窗口内最深 minpath20=%+.2f%% (@%s) —— 该窗口未触发 ebb/ice 属已知口径特性" % (
            np.expm1(worst) * 100, worst_d.date()))
        res["a2_window"] = {"phases": list(dict.fromkeys(w["phase_is"].values)),
                            "worst_minpath20": float(worst)}

    # ---------- 11. 汇总 ----------
    res["meta"] = {
        "is_window": [CAL_START, CAL_END], "oos_window": [OOS_START, OOS_END],
        "n_days_total": int(len(daily)), "n_days_oos": int(len(oos)),
        "top_decile": TOP_DECILE, "sample_gap": SAMPLE_GAP,
    }
    with open(os.path.join(OUT_DIR, "b1_results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2, default=str)
    log("")
    log("========== b1 完成 (took %.0fs) ==========" % (time.time() - t0))
    log("H2 判定: %s | H1(描述): IS=%s OOS=%s" % (
        "PASS" if h2_pass else "FAIL",
        ("%+.4f" % h1["IS"]) if "IS" in h1 else "NA",
        ("%+.4f" % h1["OOS"]) if "OOS" in h1 else "NA"))


if __name__ == "__main__":
    main()
