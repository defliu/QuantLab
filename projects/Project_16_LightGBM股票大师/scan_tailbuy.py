# coding: utf-8
"""尾盘买入口径回测（审计 R1 之后的替代方案验证）

背景：可执行 open→open 口径（T+1 开盘买、T+2 开盘卖）吃不到隔夜跳空，日均超额转负。
本方案验证"尾盘买入"：用**前一日盘后信号**，当日 14:50 尾盘以≈当日收盘价买入，
持有 N 天后尾盘卖出。这样能吃到隔夜跳空，且实盘可执行（前一日信号，当日尾盘下单）。

选股/评分/费率逻辑与 scan_rotate_cost 完全一致，仅成交时点不同：
  - 信号日：dates[i-1] 盘后（前100池 → 前10池 → 红线58 → top2 by total_new）
  - 买入日：dates[i] 14:50 尾盘，成交价 = 当日 close（近似 14:50 价格）
  - 一字板过滤：买入日 open ≥ up_limit × 0.9999 → 跳过（尾盘也买不进）
  - 卖出：持有 N 天，卖出日 14:50 尾盘，成交价 = 当日 close
  - 止损 -7% / 止盈 +15%（相对成本，逐日盯市）；滑点 0/0.1%/0.2% 三档
基准：全市场 close→close 日收益均值。
输出：data/scan_tailbuy_report.md
"""
import json
import os

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP
import qmt_config as C
import review_full as RF

HERE = DC.PROJECT_DIR
PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
MODEL = DC.model_file("_v3")
META = os.path.join(DC.DATA_DIR, "features_v3.json")
OUT_MD = os.path.join(DC.DATA_DIR, "scan_tailbuy_report.md")

THRESHOLD = 58.0
PRE_POOL = 100
TOP10 = 10
TOP = 2
STOP, TP = -0.07, 0.15
N_LIST = [1, 3, 5, 10]
SLIPS = [0.0, 0.001, 0.002]
START, END = "2024-07-01", "2026-08-14"
W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}

COMM_RATE, STAMP_RATE, TRANS_RATE = C.COMM_RATE, C.STAMP_RATE, C.TRANS_RATE


def is_sh(code):
    return code.startswith("6")


def sell_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + amt * STAMP_RATE + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def buy_fee(amt, code, slip):
    comm = max(C.COMM_MIN, amt * COMM_RATE)
    return comm + (amt * TRANS_RATE if is_sh(code) else 0.0) + amt * slip


def build_per_day():
    print("[1/3] 加载 + 逐日打分 ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "close", "up_limit",
                                                    "vol", "suspend_timing", "pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily.sort_values(["ts_code", "trade_date"])
    g = daily.groupby("ts_code")
    nxt = pd.DataFrame({
        "open_next": g["open"].shift(-1),
        "up_limit_next": g["up_limit"].shift(-1),
        "vol_next": g["vol"].shift(-1),
        "suspend_next": g["suspend_timing"].shift(-1),
    }, index=daily.index)
    daily = pd.concat([daily, nxt], axis=1)
    daily = daily.set_index(["trade_date", "ts_code"]).sort_index()

    # 全市场 close/open/up_limit 映射：(date, code) -> value
    close_map = daily["close"].to_dict()
    open_map = daily["open"].to_dict()
    up_map = daily["up_limit"].to_dict()

    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    per_day, market_avgs = {}, []
    for d in dates:
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        day["prob"] = booster.predict(day[feat_cols].astype("float32").values)
        sc = DP.compute_scorecard(day)
        for k in ("F1", "F2", "F3", "F4", "F5"):
            day[k] = sc[k].values
        idx = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"]])
        est = daily.reindex(idx)
        day["F6_new"] = [RF.score_f6(p, t) for p, t in zip(est["pe_ttm"].values, est["turnover_rate"].values)]
        day["total_new"] = sum(W[k] * day[k] for k in ("F1", "F2", "F3", "F4", "F5")).values * 10.0 + W["F6"] * day["F6_new"] * 10.0
        day["open_next"] = est["open_next"].values
        day["up_limit_next"] = est["up_limit_next"].values
        day["vol_next"] = est["vol_next"].values
        day["suspend_next"] = est["suspend_next"].values
        market_avgs.append(float(day["fwd_ret"].mean()))
        pre = day.nlargest(PRE_POOL, "prob")[["ts_code", "total_new", "fwd_ret", "prob", "open_next",
                                              "up_limit_next", "vol_next", "suspend_next"]].set_index("ts_code")
        per_day[d] = pre.nlargest(TOP10, "prob")
    return dates, per_day, pd.Series(market_avgs), close_map, open_map, up_map


def tailbuy_simulate(dates, per_day, N, slip, close_map, open_map, up_map):
    """尾盘买入：信号日 dates[i-1] → 买入日 dates[i] 尾盘（当日 close），持有 N 天尾盘卖出。"""
    M = len(dates)
    cash = 100000.0
    hold = {}  # code -> dict(value, buy_val, buy_i, invest)
    trades, daily_ret = [], []
    prev_total = 100000.0
    n_skip = 0
    for i, d in enumerate(dates):
        # 1) 用前一日信号尝试补仓（尾盘买入，成交价=当日 close）
        if i > 0:
            sig = per_day[dates[i - 1]]  # 前一日盘后信号
            while len(hold) < TOP:
                cand = sig[~sig.index.isin(hold)]
                cand = cand[cand["total_new"] >= THRESHOLD]
                if len(cand) == 0:
                    break
                # 买入日一字板（open>=up_limit）→ 买不进，剔除
                def _executable(s):
                    if s["vol_next"] is None or (isinstance(s["vol_next"], float) and np.isnan(s["vol_next"])):
                        return False
                    if s["vol_next"] <= 0:
                        return False
                    if s["suspend_next"] is not None and not (isinstance(s["suspend_next"], float) and np.isnan(s["suspend_next"])):
                        return False
                    if s["up_limit_next"] is not None and s["open_next"] is not None \
                            and not (isinstance(s["up_limit_next"], float) and np.isnan(s["up_limit_next"])) \
                            and not (isinstance(s["open_next"], float) and np.isnan(s["open_next"])) \
                            and s["open_next"] >= s["up_limit_next"]:
                        return False
                    return True
                n_before = len(cand)
                cand = cand[cand.apply(_executable, axis=1)]
                n_skip += (n_before - len(cand))
                if len(cand) == 0:
                    break
                best = cand.nlargest(1, "total_new").index[0]
                n = TOP - len(hold)
                budget = cash * 0.95 / n
                fee = buy_fee(budget, best, slip)
                invest = budget - fee
                if invest <= 0:
                    break
                cash -= budget
                hold[best] = {"value": invest, "buy_val": invest, "buy_i": i, "invest": invest}
        # 2) 持仓按当日 close 逐日盯市（买入价 = 买入日 close）
        for c in list(hold):
            h = hold[c]
            buy_close = close_map.get((dates[h["buy_i"]], c), np.nan)
            c_now = close_map.get((d, c), np.nan)
            if buy_close and c_now and buy_close > 0:
                h["value"] = h["invest"] * c_now / buy_close
        # 3) 卖出：止损/止盈/期满（卖出日尾盘，成交价=当日 close）
        for c in list(hold):
            h = hold[c]
            c_now = close_map.get((d, c), np.nan)
            if not (c_now and c_now > 0):
                del hold[c]
                continue
            ret = h["value"] / h["invest"] - 1
            if ret <= STOP or ret >= TP or (i - h["buy_i"]) >= N:
                amt = h["value"]
                cash += amt - sell_fee(amt, c, slip)
                trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                del hold[c]
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    # 期末清仓
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
    return trades, pd.Series(daily_ret), n_skip


def stats(trades, daily_ret, market_daily):
    rets = [t[2] for t in trades]
    fwd = pd.Series(rets)
    win = float((fwd > 0).mean()) if len(fwd) else np.nan
    aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
    al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
    pr = float(aw / abs(al)) if al and al != 0 else np.nan
    nav = (1 + daily_ret).cumprod()
    mdd = float((nav / nav.cummax() - 1).min())
    excess = float(daily_ret.mean() - market_daily.mean())
    return {"n_trades": len(trades), "win_rate": win, "profit_loss_ratio": pr,
            "max_drawdown": mdd, "daily_excess": excess}


def main():
    dates, per_day, market_daily, close_map, open_map, up_map = build_per_day()
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 持仓{TOP} | 止损{STOP:.0%}/止盈{TP:.0%}")
    print("[2/3] 尾盘买入模拟（持有期 × 滑点）...")
    rows = []
    for N in N_LIST:
        for slip in SLIPS:
            trades, daily_ret, n_skip = tailbuy_simulate(dates, per_day, N, slip, close_map, open_map, up_map)
            s = stats(trades, daily_ret, market_daily)
            rows.append({"N": N, "slip": slip, **s})
            print(f"    N={N} 滑点{slip:.1%}: 超额{s['daily_excess']:.3%} 胜率{s['win_rate']:.1%} "
                  f"盈亏比{s['profit_loss_ratio']:.2f} 回撤{s['max_drawdown']:.1%} 交易{s['n_trades']} 跳过{n_skip}")
    res = pd.DataFrame(rows)
    print("[3/3] 保存报告 ...")
    lines = [
        "# 尾盘买入口径回测（2026-08-23，审计 R1 替代方案验证）",
        "",
        f"> 测试期 {START} ~ {END} | 前10池+红线{THRESHOLD}+top{TOP} | 成本：佣金万2(双边,最低5元) + 印花税万5(卖出) + 过户费万0.1(沪市) | 滑点敏感性 0/0.1%/0.2%",
        "",
        "> **口径**：用前一日盘后信号，当日 14:50 尾盘买入（成交价≈当日 close），持有 N 天后尾盘卖出。可吃到隔夜跳空，实盘可执行。",
        "",
        "| 持有期 | 滑点/边 | 交易数 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['N']}天 | {r['slip']:.1%} | {r['n_trades']} | {r['win_rate']:.1%} "
            f"| {r['profit_loss_ratio']:.2f} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"    报告: {OUT_MD}")


if __name__ == "__main__":
    main()
