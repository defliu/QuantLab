# coding: utf-8
"""DPO 指标抓主升浪 —— 优化探索：能否达到实盘程度（2026-09-03）。

前置：DPO可行性评估_20260903 已证伪头条文章宣称（真实模拟胜率 27-33% vs 宣称 90%）。
本脚本在"DPO 思路"基础上，用零主观规则的可复现变体系统测试优化空间：

优化假说（源自可行性数据）：
  ① A股短期反转异常：唯一跑赢无条件基线的组是「零下金叉」（深跌超跌反弹），
     但原文买点把零下金叉排除在买点外（"零下金叉全是弱反弹"）→ 检验深跌反转。
  ② 牛市门控：bias B 的超额集中在 2015/2020 牛市 → 用市场动量 mkt60>0 门控
     （90 日 warm-up 不足支撑个股 MA200 广度门控，改用日截面等权 60 日收益均值）。
  ③ 截面分层：73 信号/日太多 → 按跌幅/量比日截面五分位看哪层有 alpha。
  ④ 退出工程：原始 simulate 的「dpo<0 清仓」对零下入场结构性失效（入场即 dpo≤0 →
     立刻退出），可行性 6-12 日平均持有可能砍掉了盈利 → 自定义死叉/止损/固定持有退出。

多比较验证纪律（backtest-expert）：~13 个变体，接受线预注册：
  fwd20 命中 ≥52% 且 均值 ≥+3.5%；fwd60 命中 ≥48% 且 均值 ≥+4.5%
  模拟（扣成本后）n≥1000、胜率 ≥45%、净均值 >0，且成本×2 后仍 >0；年度正超额 ≥8/12 年。

范式与可行性脚本同源（PIT：指标只用 t 及以前数据；信号 t 收盘 → t+1 开盘成交；
涨停开盘不可买；停牌缺口 >10 自然日剔除；同股不重叠持仓；双边成本 0.15%）。

备用数据源：按全局规则先调 data/gpsj_reader.is_available()，不可用则 [SKIP] 并标注
「单源结论（仅 astock），未经备用源交叉验证」。

用法：
  python research/dpo_optimize_20260903.py [--start 2015-01-01] [--end 2026-08-21]
"""
import argparse
import gc
import json
import os
import time

import numpy as np
import pandas as pd

from dpo_feasibility_20260903 import (
    RESULTS_DIR,
    HOLD_CAP, ROUND_COST, FWD_DAYS,
    check_gpsj, load_stock_panel, gshift,
    build_dpo, build_common, fwd_stats, summarize_fwd,
    baseline_stats, summarize_trades,
)

# ---------------------------------------------------------------------------
# 预注册接受线（多比较验证）
# ---------------------------------------------------------------------------
ACCEPT = {
    "fwd20_hit": 52.0, "fwd20_mean": 3.5,
    "fwd60_hit": 48.0, "fwd60_mean": 4.5,
    "sim_n": 1000, "sim_win": 45.0, "sim_net_mean": 0.0,
    "sim_years_pos": 8, "stress_net_mean": 0.0,
}


def accept_flags(r):
    f20 = r.get("fwd20") or {}
    f60 = r.get("fwd60") or {}
    return {
        "fwd20_accept": bool(f20.get("hit", 0) >= ACCEPT["fwd20_hit"]
                             and f20.get("mean", -99) >= ACCEPT["fwd20_mean"]),
        "fwd60_accept": bool(f60.get("hit", 0) >= ACCEPT["fwd60_hit"]
                             and f60.get("mean", -99) >= ACCEPT["fwd60_mean"]),
    }


# ---------------------------------------------------------------------------
# 市场动量门控（替代个股 MA200 广度门控；90 日 warm-up 即可用，PIT 安全）
# ---------------------------------------------------------------------------
def build_mkt_gate(close):
    mkt60 = (close / gshift(close, 60) - 1.0).groupby(level="trade_date").transform("mean")
    bull60 = mkt60 > 0.0
    print("[gate] mkt60 = 日截面等权 60 日收益均值；bull60 = mkt60>0 样本占比 %.1f%%"
          % (bull60.mean() * 100.0))
    return bull60


# ---------------------------------------------------------------------------
# 自定义退出逐笔模拟（entry 逻辑与 simulate() 逐字一致，exit 可配）
# ---------------------------------------------------------------------------
def simulate_exit(sig, df, dpo, sig_line, below3, exit_mode="deadcross+stop8",
                  stop_pct=0.08, hold_cap=HOLD_CAP, cost=ROUND_COST, fixed_hold=20):
    """信号 t 收盘 → t+1 开盘买入；退出：
      'article'        破零轴(dpo<0)/连续3日<MA20/满60日 → 当日收盘卖（原文纪律）
      'deadcross'      dpo 下穿 sig（t 收盘判定，无前视）→ t+1 开盘卖；满60日收盘卖
      'fixed'          持满 fixed_hold 交易日 → 当日收盘卖
      'stop8'          收盘 ≤ 成本价×(1-stop) → t+1 开盘卖；满60日收盘卖
      'deadcross+stop8' 两者谁先触发都 t+1 开盘卖；满60日收盘卖
    涨停开盘不可买；停牌缺口 >10 自然日剔除；同股不重叠持仓。"""
    work = pd.DataFrame({
        "open": df["open"].astype("float64"),
        "high": df["high"].astype("float64"),
        "close": df["close"].astype("float64"),
        "adj": df["adj_factor"].astype("float64"),
        "dpo": dpo.astype("float64"),
        "sig": sig.astype("bool"),
        "sigline": sig_line.astype("float64"),
        "below3": below3.astype("bool"),
    })
    work = work.swaplevel().sort_index()          # (ts_code, trade_date)
    records = []
    for code, g in work.groupby(level=0, sort=False):
        arrs = {c: g[c].to_numpy() for c in work.columns}
        dates = g.index.get_level_values(1).values
        n = len(g)
        lim = 0.195 if code[:3] in ("300", "301", "688", "689") else 0.095
        sig_pos = np.where(arrs["sig"] & np.isfinite(arrs["dpo"]))[0]
        busy_until = -1
        for i in sig_pos:
            if i <= busy_until or i + 1 >= n:
                continue
            e = i + 1
            if not np.isfinite(arrs["adj"][e]) or arrs["adj"][e] <= 0:
                continue
            if (np.datetime64(dates[e]) - np.datetime64(dates[i])) > np.timedelta64(10, "D"):
                continue
            prev_raw_close = arrs["close"][i] / arrs["adj"][i]
            raw_open = arrs["open"][e] / arrs["adj"][e]
            if prev_raw_close > 0 and raw_open / prev_raw_close - 1.0 >= lim:
                continue                            # 涨停开盘买不进
            entry_px = arrs["open"][e]
            if not np.isfinite(entry_px) or entry_px <= 0:
                continue
            hi = entry_px
            exit_row = n - 1
            sell_open = False
            if exit_mode == "fixed":
                exit_row = min(e + fixed_hold, n - 1)
                for j in range(e, exit_row + 1):
                    hj = arrs["high"][j]
                    if np.isfinite(hj) and hj > hi:
                        hi = hj
            elif exit_mode == "article":
                for j in range(e, min(e + hold_cap + 1, n)):
                    hj = arrs["high"][j]
                    if np.isfinite(hj) and hj > hi:
                        hi = hj
                    if arrs["dpo"][j] < 0 or arrs["below3"][j] or (j - e) >= hold_cap:
                        exit_row = j
                        break
            else:                                   # deadcross / stop8 / deadcross+stop8
                for j in range(e, min(e + hold_cap + 1, n)):
                    hj = arrs["high"][j]
                    if np.isfinite(hj) and hj > hi:
                        hi = hj
                    if (j - e) >= hold_cap:
                        exit_row = j
                        break
                    trig = False
                    if "deadcross" in exit_mode and np.isfinite(arrs["dpo"][j]) \
                            and np.isfinite(arrs["sigline"][j]) \
                            and arrs["dpo"][j] < arrs["sigline"][j]:
                        trig = True
                    if "stop8" in exit_mode and np.isfinite(arrs["close"][j]) \
                            and arrs["close"][j] <= entry_px * (1.0 - stop_pct):
                        trig = True
                    if trig:
                        if j + 1 < n:
                            op = arrs["open"][j + 1]
                            if np.isfinite(op) and op > 0:
                                exit_row = j + 1
                                sell_open = True
                            else:
                                exit_row = j
                        else:
                            exit_row = j
                        break
            busy_until = exit_row
            exit_px = arrs["open"][exit_row] if sell_open else arrs["close"][exit_row]
            if not np.isfinite(exit_px) or exit_px <= 0:
                exit_px = arrs["close"][exit_row]
            ret = exit_px / entry_px - 1.0 - cost
            mfe = hi / entry_px - 1.0
            records.append((code, pd.Timestamp(dates[i]), pd.Timestamp(dates[exit_row]),
                            ret * 100.0, mfe * 100.0, exit_row - e))
    tr = pd.DataFrame(records, columns=["code", "entry", "exit", "ret", "mfe", "hold"])
    return tr


# ---------------------------------------------------------------------------
# 日截面五分位分层（按因子对信号分 5 层，看哪层有 alpha）
# ---------------------------------------------------------------------------
def cross_quintile_stats(name, sig, close, factor, fwd_n=20):
    f = factor.astype("float64").replace([np.inf, -np.inf], np.nan)
    pct = f.groupby(level="trade_date").rank(pct=True)
    bucket = np.floor(pct * 5.0).astype("Int64").clip(0, 4)   # 可空整数，NaN 行由 dropna 排除
    sig_idx = sig.index[sig.fillna(False)]
    if len(sig_idx) == 0:
        print("  [%s] 无信号" % name)
        return None
    fwd = close.groupby(level="ts_code").shift(-fwd_n)
    ret = (fwd / close - 1.0) * 100.0
    sub = pd.DataFrame({
        "ret": ret.reindex(sig_idx),
        "bucket": bucket.reindex(sig_idx),
        "f": f.reindex(sig_idx),
    }).dropna()
    print("\n【截面分层 %s】(fwd%d，按 %s 日截面五分位)" % (name, fwd_n, name))
    out = {}
    for b in range(5):
        v = sub.loc[sub["bucket"] == b, "ret"]
        if len(v) == 0:
            continue
        hit = (v > 0).mean() * 100.0
        print("  Q%d  n=%5d  fwd%d 均值%+6.2f%% 中位%+6.2f%% 命中%5.1f%%"
              % (b, len(v), fwd_n, v.mean(), v.median(), hit))
        out["Q%d" % b] = {"n": int(len(v)), "mean": round(float(v.mean()), 2),
                          "median": round(float(v.median()), 2), "hit": round(float(hit), 1)}
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(start_eval, end_eval):
    gpsj_ok = check_gpsj()
    if not gpsj_ok:
        print("[gpsj][SKIP] 备用数据源不可用 → 单源结论（仅 astock），未经备用源交叉验证")

    load_start = (pd.Timestamp(start_eval) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    df = load_stock_panel(load_start, end_eval)
    st_mask = df["is_st"] == 1

    print("[ind] 计算通用指标…")
    t0 = time.time()
    ma20, vol_ratio, hhv_c10_prev, below3 = build_common(df)
    print("[ind] %.1fs" % (time.time() - t0))

    close = df["close"].astype("float64")
    vol_ok = df["vol"] > 0
    eval_mask = (df.index.get_level_values("trade_date") >= pd.Timestamp(start_eval)) & vol_ok

    baseline = baseline_stats(close, st_mask)
    bull60 = build_mkt_gate(close)

    results = {"period": [start_eval, end_eval], "baseline": baseline,
               "accept_bar": ACCEPT, "gpsj_cross_validated": gpsj_ok,
               "gate": {"bull60": "日截面等权 60 日收益均值 > 0"},
               "variants": [], "quintiles": {}, "trades": [], "stress": []}

    # 信号基础条件（深跌反转测试不用原文强制的 vol_ratio>=1.3）
    base_no_vol = (ma20.notna()) & (~st_mask) & eval_mask
    base_shrink = base_no_vol & (vol_ratio < 1.0)
    ret20c = close / gshift(close, 20) - 1.0

    for mode, mode_name in [("std", "标准DPO(ref-MA12,7)"), ("bias", "偏离式(close-MA12)")]:
        print("\n" + "=" * 72)
        print("=== DPO 口径：%s ===" % mode_name)
        dpo, sig = build_dpo(df, mode)

        dpo_p1 = gshift(dpo, 1)
        sig_p1 = gshift(sig, 1)
        gcross = (dpo > sig) & (dpo_p1 <= sig_p1)
        gc_below = gcross & (dpo_p1 <= 0)
        gc_above = gcross & (dpo_p1 > 0)
        dpo_min60 = dpo.groupby(level="ts_code").transform(
            lambda x: x.rolling(60, min_periods=60).min())

        if mode == "std":
            variants = [
                ("V1_深跌10%零下金叉", gc_below & base_no_vol & (ret20c <= -0.10)),
                ("V2_深跌15%零下金叉", gc_below & base_no_vol & (ret20c <= -0.15)),
                ("V3_深跌20%零下金叉", gc_below & base_no_vol & (ret20c <= -0.20)),
                ("V4_深跌15%缩量零下金叉", gc_below & base_shrink & (ret20c <= -0.15)),
                ("V5_DPO60日新低零下金叉", gc_below & base_no_vol & (dpo <= dpo_min60)),
                ("V6_零上金叉&牛市门控", gc_above & base_no_vol & bull60),
                ("V11_零下金叉&牛市门控", gc_below & base_no_vol & bull60),
                ("V10_深跌15%零下金叉&牛市门控",
                 gc_below & base_no_vol & (ret20c <= -0.15) & bull60),
            ]
            # 原文 B（二次金叉）要求独立构造（含 close>前10日最高 + 放量1.3）
            gc_cnt25 = gc_above.astype("int8").groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=1).sum())
            dpo_min25 = dpo.groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=25).min())
            base_cond = (ma20.notna()) & (~st_mask) & (vol_ratio >= 1.3) & eval_mask
            sig_b = gcross & (dpo > 0) & (gc_cnt25 >= 2) & (dpo_min25 > 0) \
                & (close > hhv_c10_prev) & base_cond
            variants.append(("V7_B信号&牛市门控", sig_b & bull60))
            variants.append(("V7b_B信号", sig_b))
            del gc_cnt25, dpo_min25, base_cond, sig_b
        else:
            gc_cnt25 = gc_above.astype("int8").groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=1).sum())
            dpo_min25 = dpo.groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=25).min())
            base_cond = (ma20.notna()) & (~st_mask) & (vol_ratio >= 1.3) & eval_mask
            sig_b = gcross & (dpo > 0) & (gc_cnt25 >= 2) & (dpo_min25 > 0) \
                & (close > hhv_c10_prev) & base_cond
            variants = [
                ("V8_bias_B信号&牛市门控", sig_b & bull60),
                ("V9_bias深跌15%零下金叉", gc_below & base_no_vol & (ret20c <= -0.15)),
            ]
            del gc_cnt25, dpo_min25, base_cond, sig_b

        for nm, s in variants:
            r = summarize_fwd("%s|%s" % (mode_name, nm), fwd_stats(s, close), baseline)
            if r:
                r["dpo_mode"] = mode
                r["accept"] = accept_flags(r)
                results["variants"].append(r)
            del s
            gc.collect()

        # 截面分层：std 零下金叉 按跌幅 / 量比
        if mode == "std":
            g_below_all = gc_below & base_no_vol
            results["quintiles"]["std零下金叉_按ret20"] = cross_quintile_stats(
                "ret20", g_below_all, close, ret20c)
            results["quintiles"]["std零下金叉_按量比"] = cross_quintile_stats(
                "vol_ratio", g_below_all, close, vol_ratio)
            del g_below_all

        # 逐笔模拟
        if mode == "std":
            v2 = gc_below & base_no_vol & (ret20c <= -0.15)
            v3 = gc_below & base_no_vol & (ret20c <= -0.20)
            v7 = None
            gc_cnt25 = gc_above.astype("int8").groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=1).sum())
            dpo_min25 = dpo.groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=25).min())
            base_cond = (ma20.notna()) & (~st_mask) & (vol_ratio >= 1.3) & eval_mask
            sig_b = gcross & (dpo > 0) & (gc_cnt25 >= 2) & (dpo_min25 > 0) \
                & (close > hhv_c10_prev) & base_cond
            v7 = sig_b & bull60
            v11b = v2 & bull60
            sims = [
                ("S1_V2深跌15%_死叉+止损8%退出", v2, "deadcross+stop8", None),
                ("S2_V2深跌15%_固定20日退出", v2, "fixed", 20),
                ("S2b_V2深跌15%_固定15日退出", v2, "fixed", 15),
                ("S2c_V2深跌15%_固定30日退出", v2, "fixed", 30),
                ("S3_V2深跌15%_原文退出(对照)", v2, "article", None),
                ("S4_V3深跌20%_死叉+止损8%退出", v3, "deadcross+stop8", None),
                ("S4b_V3深跌20%_固定20日退出", v3, "fixed", 20),
                ("S5_V7_B信号&牛市门控_原文退出", v7, "article", None),
                ("S7_V2深跌15%&牛市门控_死叉+止损8%", v11b, "deadcross+stop8", None),
            ]
            del v2, v3, v7, v11b, gc_cnt25, dpo_min25, base_cond, sig_b
        else:
            gc_cnt25 = gc_above.astype("int8").groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=1).sum())
            dpo_min25 = dpo.groupby(level="ts_code").transform(
                lambda x: x.rolling(25, min_periods=25).min())
            base_cond = (ma20.notna()) & (~st_mask) & (vol_ratio >= 1.3) & eval_mask
            sig_b = gcross & (dpo > 0) & (gc_cnt25 >= 2) & (dpo_min25 > 0) \
                & (close > hhv_c10_prev) & base_cond
            sims = [("S6_bias_B信号&牛市门控_原文退出", sig_b & bull60, "article", None)]
            del gc_cnt25, dpo_min25, base_cond, sig_b

        for nm, s, ex, fh in sims:
            tr = simulate_exit(s, df, dpo, sig, below3, exit_mode=ex, fixed_hold=fh or 20)
            r = summarize_trades("%s|%s" % (mode_name, nm), tr)
            if r:
                r["dpo_mode"] = mode
                r["exit_mode"] = ex if ex != "fixed" else "fixed(%d)" % (fh or 20)
                r["accept"] = {
                    "sim_accept": bool(r["n_trades"] >= ACCEPT["sim_n"]
                                       and r["win"] >= ACCEPT["sim_win"]
                                       and r["ret_mean"] > ACCEPT["sim_net_mean"]),
                    "years_pos": int(sum(1 for v in r["yearly"].values()
                                         if v["mean"] > 0)),
                }
                results["trades"].append(r)
                # 压力测试：finalist 成本×2
                if nm in ("S1_V2深跌15%_死叉+止损8%退出", "S4_V3深跌20%_死叉+止损8%退出",
                          "S5_V7_B信号&牛市门控_原文退出", "S6_bias_B信号&牛市门控_原文退出",
                          "S2_V2深跌15%_固定20日退出", "S4b_V3深跌20%_固定20日退出"):
                    tr2 = simulate_exit(s, df, dpo, sig, below3, exit_mode=ex,
                                        fixed_hold=fh or 20, cost=0.003)
                    r2 = summarize_trades("%s|%s(成本×2)" % (mode_name, nm), tr2)
                    if r2:
                        r2["dpo_mode"] = mode
                        r2["exit_mode"] = ex if ex != "fixed" else "fixed(%d)" % (fh or 20)
                        r2["cost_x2"] = True
                        r2["stress_accept"] = bool(r2["ret_mean"] > ACCEPT["stress_net_mean"])
                        results["stress"].append(r2)
                    del tr2
                    gc.collect()
            del tr
            gc.collect()
            del s
            gc.collect()

        del dpo, sig, gcross, gc_below, gc_above, dpo_min60, dpo_p1, sig_p1
        gc.collect()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = RESULTS_DIR + "/DPO优化探索_20260903.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n[out] %s" % out_json)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2026-08-21")
    args = ap.parse_args()
    run(args.start, args.end)


if __name__ == "__main__":
    main()
