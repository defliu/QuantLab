# coding: utf-8
"""任务3：历史引擎(scan_rotate_cost_real) vs DE引擎(de_r8_common) 逐日对比与差异定位。

同模型 1964t、同配置 K2/N15/红线60/live_trail。输出 out/cmp_results.json。
只读源码/数据/模型；只写 out/。
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
HERE = r"D:\QuantLab\data\schedules\de_r8_scripts"
OUT = os.path.join(HERE, "out")
PANEL = os.path.join(PROJ, "data", "feature_panel_v3_enh2_n3_bt.parquet")
MODEL_1964 = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
META_1964 = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260825.json")

os.environ["BT_PANEL"] = PANEL
os.environ["BT_MODEL"] = MODEL_1964
os.environ["BT_META"] = META_1964
os.environ["BT_THRESHOLD"] = "60"
os.environ["BT_TOP"] = "2"
os.environ["BT_EXIT"] = "live_trail"
os.environ["BT_N_LIST"] = "15"
os.environ["BT_SLIPS"] = "0.001"
os.environ["BT_OUT"] = os.path.join(OUT, "cmp_unused_report.md")
os.chdir(PROJ)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import scan_rotate_cost_real as HIST  # noqa: E402
import de_r8_common as DE  # noqa: E402
import scorecard_real as SR  # noqa: E402

assert HIST.EXIT_MODE == "live_trail" and HIST.TOP == 2 and HIST.THRESHOLD == 60.0

R = {}


def _ex(ret, bench):
    return float(ret.mean() - bench)


# ---------------- 历史引擎 instrumented trace（忠实复刻 exec_ok=True + live_trail 分支） ----------------
def simulate_hist_trace(dates, per_day, N, slip, open_map):
    M = len(dates)
    cash = 100000.0
    hold = {}
    trades, daily_ret, trace = [], [], []
    prev_total = 100000.0
    for i, d in enumerate(dates):
        row = per_day[d]
        if i > 0:
            for c in list(hold):
                h = hold[c]
                o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
                o_cur = open_map.get((d, c))
                if o_buy and o_cur and o_buy > 0:
                    h["value"] = h["invest"] * o_cur / o_buy
        sells_today = []
        for c in list(hold):
            h = hold[c]
            o_buy = open_map.get((dates[h["buy_i"] + 1], c)) if h["buy_i"] + 1 < len(dates) else None
            o_cur = open_map.get((d, c))
            if not (o_buy and o_cur and o_buy > 0):
                h["missing_days"] = h.get("missing_days", 0) + 1
                if h["missing_days"] >= HIST.DELIST_DAYS:
                    amt = h["value"]
                    cash += amt * (1 - HIST.DELIST_LOSS) - HIST.sell_fee(amt, c, slip)
                    trades.append({"buy_i": h["buy_i"], "sell_i": i, "ret": amt * (1 - HIST.DELIST_LOSS) / h["buy_val"] - 1,
                                   "code": c, "reason": "DELIST"})
                    sells_today.append((c, "DELIST"))
                    del hold[c]
                continue
            h["missing_days"] = 0
            hi = HIST._HIGH_MAP.get((d, c))
            peak = h.get("peak")
            if not peak or peak <= 0:
                peak = o_buy
            if (i - h["buy_i"]) >= 2 and hi and hi > 0 and not (isinstance(hi, float) and np.isnan(hi)):
                h["peak"] = max(peak, hi)
            ret = o_cur / o_buy - 1
            down = HIST._DOWN_LIMIT_MAP.get((d, c))
            one_word_down = down is not None and not (isinstance(down, float) and np.isnan(down)) and o_cur <= down + 1e-9
            if one_word_down:
                continue
            sell, reason = False, ""
            if (i - h["buy_i"]) >= N + 1:
                sell, reason = True, "MATURE"
            elif (i - h["buy_i"]) < 2:
                sell, reason = False, ""
            else:
                trail_act = peak >= o_buy * (1 + HIST.TRAIL_ACTIVATE_PCT)
                trail_hit = trail_act and o_cur <= max(o_buy, peak * (1 - HIST.TRAIL_PCT))
                if ret <= HIST.STOP:
                    sell, reason = True, "STOP"
                elif ret >= HIST.TP:
                    sell, reason = True, "TP"
                elif trail_hit:
                    sell, reason = True, "TRAIL"
            if sell:
                amt = h["value"]
                cash += amt - HIST.sell_fee(amt, c, slip)
                trades.append({"buy_i": h["buy_i"], "sell_i": i, "ret": h["value"] / h["buy_val"] - 1,
                               "code": c, "reason": reason})
                sells_today.append((c, reason))
                del hold[c]
        buys_today = []
        while len(hold) < HIST.TOP:
            cand = row[~row.index.isin(hold)]
            cand = cand[cand["total_new"] >= HIST.THRESHOLD]
            if len(cand) == 0:
                break
            cand = cand[cand.apply(HIST._executable, axis=1)]
            if len(cand) == 0:
                break
            best = cand.nlargest(1, "total_new").index[0]
            n = HIST.TOP - len(hold)
            budget = cash * 0.95 / n
            fee = HIST.buy_fee(budget, best, slip)
            invest = budget - fee
            if invest <= 0:
                break
            cash -= budget
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
            buys_today.append(best)
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
        trace.append({"i": i, "date": d, "hold": sorted(hold.keys()),
                      "buys": list(buys_today), "sells": list(sells_today)})
    for c, h in hold.items():
        trades.append({"buy_i": h["buy_i"], "sell_i": M, "ret": h["value"] / h["buy_val"] - 1,
                       "code": c, "reason": "OPEN_END"})
    return trades, pd.Series(daily_ret), trace


# ---------------- DE 引擎 instrumented trace（忠实复刻 simulate 默认参数：open 买 + mature open 卖） ----------------
def simulate_de_trace(dates, per_day, K, N, ctx):
    pxmap = ctx["px"]
    cash = DE.CAPITAL0
    prev_total = DE.CAPITAL0
    hold = {}
    trades, daily_ret, trace = [], [], []
    reasons = {}
    M = len(dates)
    for i, d in enumerate(dates):
        for c in list(hold):
            h = hold[c]
            if h.get("pending"):
                v = pxmap.get(c, {}).get(d)
                if v is None:
                    continue
                amt = h["value"]
                cash += amt - DE.sell_fee(amt, c)
                trades.append({"buy_i": h["buy_i"], "sell_i": i, "ret": amt / h["buy_val"] - 1,
                               "code": c, "reason": "MATURE"})
                del hold[c]
        row = per_day.get(d)
        for c in hold:
            h = hold[c]
            v = pxmap.get(c, {}).get(d)
            if v is not None and h["px_buy"] > 0:
                h["value"] = h["invest"] * v[0] / h["px_buy"]
        sells_today = []
        for c in list(hold):
            h = hold[c]
            if h.get("pending"):
                continue
            v = pxmap.get(c, {}).get(d)
            if v is None:
                h["missing"] = h.get("missing", 0) + 1
                if h["missing"] >= DE.DELIST_DAYS:
                    amt = h["value"] * (1 - DE.DELIST_LOSS)
                    cash += amt - DE.sell_fee(amt, c)
                    trades.append({"buy_i": h["buy_i"], "sell_i": i, "ret": amt / h["buy_val"] - 1,
                                   "code": c, "reason": "DELIST"})
                    sells_today.append((c, "DELIST"))
                    del hold[c]
                continue
            h["missing"] = 0
            o_cur, hi, cl, dn = v
            if not (h["px_buy"] > 0):
                continue
            peak = h.get("peak")
            if not peak or peak <= 0:
                peak = h["px_buy"]
            if (i - h["buy_i"]) >= 2 and np.isfinite(hi) and hi > 0:
                h["peak"] = max(peak, hi)
            ret = o_cur / h["px_buy"] - 1
            if np.isfinite(dn) and o_cur <= dn + 1e-9:
                reasons["SKIP_DOWN"] = reasons.get("SKIP_DOWN", 0) + 1
                continue
            sell, reason = False, ""
            if (i - h["buy_i"]) >= N + 1:
                sell, reason = True, "MATURE"
            elif (i - h["buy_i"]) < 2:
                sell = False
            else:
                trail_act = peak >= h["px_buy"] * (1 + DE.TRAIL_ACT)
                trail_hit = trail_act and o_cur <= max(h["px_buy"], peak * (1 - DE.TRAIL_PCT))
                if ret <= DE.STOP:
                    sell, reason = True, "STOP"
                elif ret >= DE.TP:
                    sell, reason = True, "TP"
                elif trail_hit:
                    sell, reason = True, "TRAIL"
            if sell:
                amt = h["value"]
                cash += amt - DE.sell_fee(amt, c)
                trades.append({"buy_i": h["buy_i"], "sell_i": i, "ret": amt / h["buy_val"] - 1,
                               "code": c, "reason": reason})
                reasons[reason] = reasons.get(reason, 0) + 1
                sells_today.append((c, reason))
                del hold[c]
        buys_today = []
        if row is not None and len(row) > 0 and i + 1 < M:
            buy_date = dates[i + 1]
            for ri in range(len(row)):
                if len(hold) >= K:
                    break
                code = row.index[ri]
                if code in hold:
                    continue
                r = row.iloc[ri]
                if not DE._base_exec(r):
                    continue
                v = pxmap.get(code, {}).get(buy_date)
                px = v[0] if v is not None else None
                if px is None or not np.isfinite(px) or px <= 0:
                    continue
                n = K - len(hold)
                budget = cash * 0.95 / n
                fee = DE.buy_fee(budget, code)
                invest = budget - fee
                if invest <= 0:
                    break
                cash -= budget
                hold[code] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest, "px_buy": px}
                buys_today.append(code)
        tot = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(tot / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = tot
        trace.append({"i": i, "date": d, "hold": sorted(hold.keys()),
                      "buys": list(buys_today), "sells": list(sells_today)})
    for c, h in hold.items():
        trades.append({"buy_i": h["buy_i"], "sell_i": M, "ret": h["value"] / h["buy_val"] - 1,
                       "code": c, "reason": "OPEN_END"})
    return trades, pd.Series(daily_ret), trace, reasons


def ann(ret):
    nav = (1 + ret).cumprod()
    return float(nav.iloc[-1] ** (252.0 / len(ret)) - 1)


# ==================== 主流程 ====================
print("[1] 历史引擎 build_per_day（1964t）...", flush=True)
dates_h, per_day_h, mkt_h, open_map = HIST.build_per_day()
HIST._ATR_MAP.clear()
HIST._MA5_MAP.clear()
bench = float(mkt_h.mean())
R["bench_daily"] = bench
R["bench_annual"] = float((1 + bench) ** 252 - 1)

tr_off, ret_off, _ = HIST.simulate(dates_h, per_day_h, 15, 0.001, open_map, exec_ok=True)
R["H0_official"] = {"n_trades": len(tr_off), "mean_daily": float(ret_off.mean()),
                    "excess": _ex(ret_off, bench), "annual": ann(ret_off)}
print("    H0 官方 simulate: 超额 %.4f%%/日 年化 %.1f%% 交易%d" % (
    R["H0_official"]["excess"] * 100, R["H0_official"]["annual"] * 100, len(tr_off)), flush=True)

print("[2] DE 引擎 ctx + pools（1964t）...", flush=True)
DE.STRATS["G2"]["model"] = MODEL_1964
DE.STRATS["G2"]["meta"] = META_1964
uni = DE.load_universe()
ctx = DE.build_daily(uni, DE.MAIN_START, DE.MAIN_END)
dates_d, per_day_d, pstats = DE.build_pools("G2", DE.MAIN_START, DE.MAIN_END, ctx)
assert list(dates_h) == list(dates_d), "两引擎交易日历不一致!"
R["D_pools"] = {"avg_passed": pstats["avg_passed"], "n_days": pstats["n_days"]}

tr_d_off, ret_d_off, info_d_off = DE.simulate(dates_d, per_day_d, 2, 15, ctx)
R["D0_official"] = {"n_trades": len(tr_d_off), "mean_daily": float(ret_d_off.mean()),
                    "excess": _ex(ret_d_off, bench), "annual": ann(ret_d_off),
                    "reasons": info_d_off["reasons"]}
print("    D0 官方 simulate: 超额 %.4f%%/日 年化 %.1f%% 交易%d" % (
    R["D0_official"]["excess"] * 100, R["D0_official"]["annual"] * 100, len(tr_d_off)), flush=True)

# ---------------- traces + 验证 ----------------
print("[3] instrumented traces + 与官方 simulate 一致性校验 ...", flush=True)
tr_h, ret_h, trace_h = simulate_hist_trace(dates_h, per_day_h, 15, 0.001, open_map)
assert len(tr_h) == len(tr_off) and abs(ret_h.mean() - ret_off.mean()) < 1e-10, "hist trace 与官方不一致"
tr_d, ret_d, trace_d, reasons_d = simulate_de_trace(dates_d, per_day_d, 2, 15, ctx)
assert len(tr_d) == len(tr_d_off) and abs(ret_d.mean() - ret_d_off.mean()) < 1e-10, "DE trace 与官方不一致"
print("    trace 校验 PASS", flush=True)

# ---------------- suspend_timing 证据 ----------------
print("[4] suspend_timing 空字符串证据 ...", flush=True)
sn = ctx["nxt"]["suspend_next"]
isna = sn.isna()
isblank = (~isna) & (sn.astype(str).str.strip() == "")
issusp = (~isna) & (~sn.astype(str).str.strip().isin([""]))
mo = sn.index.get_level_values(0).to_period("M")
susp_tbl = pd.DataFrame({"na": isna.values, "blank": isblank.values, "susp": issusp.values},
                        index=mo).groupby(level=0).mean()
R["suspend_monthly"] = {str(k): {"nan_frac": float(v["na"]), "blank_frac": float(v["blank"]),
                                 "susp_frac": float(v["susp"])} for k, v in susp_tbl.iterrows()}
print(susp_tbl.tail(20).to_string(), flush=True)

_exec_orig = HIST._executable


def _exec_fixed(s):
    if s["vol_next"] is None or (isinstance(s["vol_next"], float) and np.isnan(s["vol_next"])):
        return False
    if s["vol_next"] <= 0:
        return False
    sv = s["suspend_next"]
    if sv is not None and not (isinstance(sv, float) and np.isnan(sv)) and str(sv).strip() != "":
        return False
    if s["up_limit_next"] is not None and s["open_next"] is not None \
            and not (isinstance(s["up_limit_next"], float) and np.isnan(s["up_limit_next"])) \
            and not (isinstance(s["open_next"], float) and np.isnan(s["open_next"])) \
            and s["open_next"] >= s["up_limit_next"]:
        return False
    return True


# 历史引擎 _executable（原版）对 2026-08 '' 的杀伤：按月统计 top10 候选可执行比例
rows_exec = []
for d in dates_h:
    r = per_day_h[d]
    if len(r) == 0:
        continue
    ok_orig = r.apply(_exec_orig, axis=1).mean()
    ok_fix = r.apply(_exec_fixed, axis=1).mean()
    rows_exec.append({"month": pd.Timestamp(d).strftime("%Y-%m"), "ok_orig": float(ok_orig), "ok_fix": float(ok_fix)})
ex_m = pd.DataFrame(rows_exec).groupby("month").mean()
R["hist_exec_ok_monthly"] = {k: {"orig": float(v["ok_orig"]), "fixed": float(v["ok_fix"])}
                             for k, v in ex_m.iterrows()}
print("    2026-06 之后 hist 原版可执行率:", flush=True)
print(ex_m.loc[[m for m in ex_m.index if m >= "2026-05"]].to_string(), flush=True)

# ---------------- 消融：H0_slip0 / H1(suspend修复) / H2(DE候选) / D1(hist候选) ----------------
print("[5] 消融链 ...", flush=True)
tr, ret, _ = simulate_hist_trace(dates_h, per_day_h, 15, 0.0, open_map)
R["H0_slip0"] = {"n_trades": len(tr), "mean_daily": float(ret.mean()), "excess": _ex(ret, bench), "annual": ann(ret)}
print("    H0_slip0: 超额 %.4f%%/日" % (R["H0_slip0"]["excess"] * 100), flush=True)

HIST._executable = _exec_fixed
tr, ret, _ = simulate_hist_trace(dates_h, per_day_h, 15, 0.001, open_map)
R["H1_suspendfix"] = {"n_trades": len(tr), "mean_daily": float(ret.mean()), "excess": _ex(ret, bench), "annual": ann(ret)}
print("    H1_suspendfix: 超额 %.4f%%/日" % (R["H1_suspendfix"]["excess"] * 100), flush=True)

per_day_de_as_hist = {}
for d in dates_d:
    r = per_day_d[d]
    per_day_de_as_hist[d] = r.rename(columns={"total": "total_new", "rank_score": "prob"})
tr_h2, ret_h2, trace_h2 = simulate_hist_trace(dates_d, per_day_de_as_hist, 15, 0.001, open_map)
R["H2_de_cands"] = {"n_trades": len(tr_h2), "mean_daily": float(ret_h2.mean()), "excess": _ex(ret_h2, bench), "annual": ann(ret_h2)}
print("    H2_de_cands: 超额 %.4f%%/日" % (R["H2_de_cands"]["excess"] * 100), flush=True)

tr, ret, _ = simulate_hist_trace(dates_d, per_day_de_as_hist, 15, 0.0, open_map)
R["H2_slip0"] = {"n_trades": len(tr), "mean_daily": float(ret.mean()), "excess": _ex(ret, bench), "annual": ann(ret)}
print("    H2_slip0: 超额 %.4f%%/日" % (R["H2_slip0"]["excess"] * 100), flush=True)

per_day_hist_as_de = {}
for d in dates_h:
    r = per_day_h[d]
    r = r[r["total_new"] >= 60.0]
    rr = pd.DataFrame({
        "total": r["total_new"].values, "rank_score": r["prob"].values,
        "open_next": r["open_next"].values, "up_limit_next": r["up_limit_next"].values,
        "vol_next": r["vol_next"].values, "suspend_next": r["suspend_next"].values,
    }, index=r.index)
    per_day_hist_as_de[d] = rr.sort_values("total", ascending=False)
tr_d1, ret_d1, trace_d1, reasons_d1 = simulate_de_trace(dates_d, per_day_hist_as_de, 2, 15, ctx)
R["D1_hist_cands"] = {"n_trades": len(tr_d1), "mean_daily": float(ret_d1.mean()), "excess": _ex(ret_d1, bench),
                      "annual": ann(ret_d1), "reasons": reasons_d1}
print("    D1_hist_cands: 超额 %.4f%%/日" % (R["D1_hist_cands"]["excess"] * 100), flush=True)

# ---------------- 候选集合逐日对比 ----------------
print("[6] 候选集合逐日对比 ...", flush=True)
n_all_top10_pass = 0
n_set_diff = 0
n_choice_diff = 0
set_diff_months = {}
for i, d in enumerate(dates_h):
    rh = per_day_h[d]
    rh_pass = rh[rh["total_new"] >= 60.0]
    rd = per_day_d[d]
    if len(rh) and (rh["total_new"] >= 60.0).all():
        n_all_top10_pass += 1
    sh, sd = set(rh_pass.index), set(rd.index)
    if sh != sd:
        n_set_diff += 1
        m = pd.Timestamp(d).strftime("%Y-%m")
        set_diff_months[m] = set_diff_months.get(m, 0) + 1
    bh = trace_h[i]["buys"][0] if trace_h[i]["buys"] else None
    bd = trace_d[i]["buys"][0] if trace_d[i]["buys"] else None
    if (bh or bd) and bh != bd:
        n_choice_diff += 1
R["cand"] = {"n_days": len(dates_h), "n_top10_all_pass": n_all_top10_pass,
             "n_set_diff": n_set_diff, "n_first_buy_diff": n_choice_diff,
             "set_diff_by_month": set_diff_months,
             "avg_hist_passed": float(np.mean([int((per_day_h[d]["total_new"] >= 60.0).sum()) for d in dates_h])),
             "avg_de_row": float(np.mean([len(per_day_d[d]) for d in dates_d]))}
print("    hist top10 全过线 %d 天 / 候选集不同 %d 天 / 首选不同 %d 天" % (
    n_all_top10_pass, n_set_diff, n_choice_diff), flush=True)

# DE 买入来源：top10-by-prob 内 vs 深挖（prob rank 11-100）
top10_codes = {d: set(per_day_h[d].index) for d in dates_h}
de_buys_top, de_buys_deep = 0, 0
de_top_rets, de_deep_rets = [], []
for t in tr_d:
    d = dates_d[t["buy_i"]]
    if t["code"] in top10_codes[d]:
        de_buys_top += 1
        de_top_rets.append(t["ret"])
    else:
        de_buys_deep += 1
        de_deep_rets.append(t["ret"])
R["de_buy_source"] = {"from_top10": de_buys_top, "from_deep": de_buys_deep,
                      "top10_mean_ret": float(np.mean(de_top_rets)) if de_top_rets else None,
                      "deep_mean_ret": float(np.mean(de_deep_rets)) if de_deep_rets else None,
                      "top10_win": float(np.mean(np.array(de_top_rets) > 0)) if de_top_rets else None,
                      "deep_win": float(np.mean(np.array(de_deep_rets) > 0)) if de_deep_rets else None}
print("    DE 买入来源: top10内 %d 笔(均ret %.2f%%) / 深挖 %d 笔(均ret %.2f%%)" % (
    de_buys_top, (R["de_buy_source"]["top10_mean_ret"] or 0) * 100,
    de_buys_deep, (R["de_buy_source"]["deep_mean_ret"] or 0) * 100), flush=True)

# hist 买入（全在 top10 内）的交易质量对照
R["hist_trades_quality"] = {"mean_ret": float(np.mean([t["ret"] for t in tr_h])),
                            "win": float(np.mean([t["ret"] > 0 for t in tr_h])),
                            "reasons": pd.Series([t["reason"] for t in tr_h]).value_counts().to_dict()}
R["de_trades_quality"] = {"mean_ret": float(np.mean([t["ret"] for t in tr_d])),
                          "win": float(np.mean([t["ret"] > 0 for t in tr_d])),
                          "reasons": pd.Series([t["reason"] for t in tr_d]).value_counts().to_dict()}

# ---------------- 逐日持仓/收益差异定位 ----------------
print("[7] 逐日持仓差异定位 ...", flush=True)
div_days = []
for i in range(len(dates_h)):
    if set(trace_h[i]["hold"]) != set(trace_d[i]["hold"]):
        div_days.append(i)
first_div = div_days[0] if div_days else None
R["trace"] = {"n_hold_diff_days": len(div_days), "first_div_i": first_div,
              "first_div_date": str(pd.Timestamp(dates_h[first_div]).date()) if first_div is not None else None}
print("    持仓不同的天数 %d / 首个分歧日 %s" % (
    len(div_days), R["trace"]["first_div_date"]), flush=True)

diff = (ret_h - ret_d)
top_diff = diff.abs().nlargest(10).index.tolist()
R["top_ret_diff_days"] = []
for i in top_diff:
    R["top_ret_diff_days"].append({
        "date": str(pd.Timestamp(dates_h[i]).date()), "i": int(i),
        "ret_h": float(ret_h.iloc[i]), "ret_d": float(ret_d.iloc[i]),
        "hold_h": trace_h[i]["hold"], "hold_d": trace_d[i]["hold"],
        "buys_h": trace_h[i]["buys"], "buys_d": trace_d[i]["buys"],
        "sells_h": trace_h[i]["sells"], "sells_d": trace_d[i]["sells"]})

# 首分歧日候选明细表
if first_div is not None:
    d0 = dates_h[first_div]
    rh = per_day_h[d0][["total_new", "prob", "open_next", "up_limit_next", "vol_next", "suspend_next"]].copy()
    rh["exec_orig"] = rh.apply(_exec_orig, axis=1)
    rh["exec_fixed"] = rh.apply(_exec_fixed, axis=1)
    rh["prob_rank"] = range(1, len(rh) + 1)
    rd = per_day_d[d0].copy()
    rows = []
    for code in sorted(set(rh.index) | set(rd.index)):
        hrow = rh.loc[code] if code in rh.index else None
        drow = rd.loc[code] if code in rd.index else None
        rows.append({
            "code": code,
            "in_hist_top10": hrow is not None,
            "in_de_row": drow is not None,
            "prob": float(hrow["prob"]) if hrow is not None else (float(drow["rank_score"]) if drow is not None else None),
            "prob_rank": int(hrow["prob_rank"]) if hrow is not None else None,
            "total": float(hrow["total_new"]) if hrow is not None else (float(drow["total"]) if drow is not None else None),
            "hist_exec": bool(hrow["exec_orig"]) if hrow is not None else None,
            "de_exec": bool(DE._base_exec(drow)) if drow is not None else None,
        })
    R["first_div_day_table"] = {"date": str(pd.Timestamp(d0).date()),
                                "hist_bought": trace_h[first_div]["buys"],
                                "de_bought": trace_d[first_div]["buys"],
                                "rows": rows}
    print("    首分歧日明细: hist买 %s / DE买 %s" % (
        trace_h[first_div]["buys"], trace_d[first_div]["buys"]), flush=True)

# ---------------- 评分卡输入差异验证（全面板 day vs top100 pool） ----------------
print("[8] 评分卡输入差异验证（full day vs top100 pool）...", flush=True)
meta = json.load(open(META_1964, encoding="utf-8"))
R["meta_keys"] = sorted(meta.keys())
for k in ("train_start", "train_end", "train_window", "dates"):
    if k in meta:
        R["meta_" + k] = str(meta[k])
feats = meta["feature_cols"]
booster = lgb.Booster(model_file=MODEL_1964)
cols = list(dict.fromkeys(feats + ["ts_code", "trade_date", "main_net", "industry_pct", "volume_ratio"]))
pan = pd.read_parquet(PANEL, columns=cols)
pan["trade_date"] = pd.to_datetime(pan["trade_date"])
R["panel_ts_code_dtype"] = str(pan["ts_code"].dtype)
check_days = []
if first_div is not None:
    check_days.append(pd.Timestamp(dates_h[first_div]))
mid = pd.Timestamp(dates_h[len(dates_h) // 2])
if mid not in check_days:
    check_days.append(mid)
R["scorecard_check"] = {}
for d in check_days:
    day = pan[pan["trade_date"] == d].copy()
    day["prob"] = booster.predict(day[feats].astype("float32").values)
    pool = day.nlargest(100, "prob").copy()
    idx_f = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"].astype(str)])
    est_f = ctx["est"].reindex(idx_f)
    idx_p = pd.MultiIndex.from_arrays([pool["trade_date"], pool["ts_code"].astype(str)])
    est_p = ctx["est"].reindex(idx_p)
    sc_f = SR.compute_real_scorecard(day, est_f)
    sc_p = SR.compute_real_scorecard(pool, est_p)
    sub_f = sc_f.loc[pool.index]
    sub_p = sc_p
    chk = {"n_day": int(len(day)), "n_pool": int(len(pool))}
    for c in ("F1", "F2", "F3", "F4", "F5", "F6", "total_new_real"):
        chk["maxdiff_" + c] = float(np.nanmax(np.abs(sub_f[c].values - sub_p[c].values))) if len(pool) else 0.0
    R["scorecard_check"][str(d.date())] = chk
    print("    %s: 全面板%d只 vs pool%d只, total maxdiff %.6g" % (
        d.date(), len(day), len(pool), chk["maxdiff_total_new_real"]), flush=True)

# 两个引擎候选池（top100 by prob）本身是否一致 → prob 一致性
print("[9] 收尾汇总 ...", flush=True)
R["summary"] = {
    "H0_hist_baseline_excess": R["H0_official"]["excess"],
    "D0_de_baseline_excess": R["D0_official"]["excess"],
    "gap_pp_per_day": (R["H0_official"]["excess"] - R["D0_official"]["excess"]) * 100,
    "contrib_suspend": (R["H1_suspendfix"]["excess"] - R["H0_official"]["excess"]) * 100,
    "contrib_candidates_H_side": (R["H2_de_cands"]["excess"] - R["H1_suspendfix"]["excess"]) * 100,
    "contrib_candidates_D_side": (R["D1_hist_cands"]["excess"] - R["D0_official"]["excess"]) * 100,
    "residual_accounting": (R["D0_official"]["excess"] - R["H2_slip0"]["excess"]) * 100,
    "contrib_slip": (R["H0_slip0"]["excess"] - R["H0_official"]["excess"]) * 100,
}

with open(os.path.join(OUT, "cmp_results.json"), "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=2, default=str, allow_nan=True)
print(json.dumps(R["summary"], ensure_ascii=False, indent=2), flush=True)
print("DONE -> cmp_results.json", flush=True)
