# coding: utf-8
"""含真实交易成本的轮动回测：基于用户实盘费率重估最优持有期。

实盘成本（用户提供，2026）：
  佣金：双边，费率约 0.02%（万2），单笔不足 5 元按 5 元（本策略单笔约4.75万>2.5万，按费率计）
  印花税：仅卖出单边 0.05%（万5）
  过户费：沪市(60开头)买卖双向 0.001%（万0.1）；深市(00/30)不收取
滑点：实盘市价单存在，做敏感性 0 / 0.1% / 0.2%（单边）

资金模拟：初始 1.0，满仓 TOP=2 等权，留 5% 缓冲；换仓扣双边费用；止损-7%/止盈+15%（累计）。
口径：模型前100池 → prob 前10 候选池 → total_new>=58 红线 → 排序 total_new（实盘同款）。

用法：python scan_rotate_cost.py
输出：data/scan_rotate_cost_report.md
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
OUT_MD = os.path.join(DC.DATA_DIR, "scan_rotate_cost_report.md")

THRESHOLD = 58.0
PRE_POOL = 100
TOP10 = 10
TOP = 2
STOP, TP = -0.07, 0.15
N_LIST = [1, 3, 5, 10]
SLIPS = [0.0, 0.001, 0.002]  # 单边滑点
START, END = "2024-07-01", "2026-08-14"
W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}

COMM_RATE, STAMP_RATE, TRANS_RATE = C.COMM_RATE, C.STAMP_RATE, C.TRANS_RATE  # 统一取 qmt_config（实盘口径）


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
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily = daily.set_index(["trade_date", "ts_code"])

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
        market_avgs.append(float(day["fwd_ret"].mean()))
        pre = day.nlargest(PRE_POOL, "prob")[["ts_code", "total_new", "fwd_ret", "prob"]].set_index("ts_code")
        per_day[d] = pre.nlargest(TOP10, "prob")
    return dates, per_day, pd.Series(market_avgs)


def simulate(dates, per_day, N, slip):
    """资金模拟：真实金额(初始10万)，满仓2只，换仓扣真实费用。返回 trades 与日收益序列。"""
    M = len(dates)
    cash = 100000.0  # 真实金额，佣金最低5元才正确生效
    hold = {}      # code -> dict(value=市值, buy_val=买入成本市值, buy_i)
    trades, daily_ret = [], []
    prev_total = 100000.0
    for i, d in enumerate(dates):
        row = per_day[d]
        if i > 0:
            prev = per_day[dates[i - 1]]
            for c in list(hold):
                r = float(prev.loc[c, "fwd_ret"]) if c in prev.index else 0.0
                hold[c]["value"] *= (1 + r)
        # 卖出：止损/止盈/期满
        for c in list(hold):
            h = hold[c]
            if h["value"] / h["buy_val"] - 1 <= STOP or h["value"] / h["buy_val"] - 1 >= TP or (i - h["buy_i"]) >= N:
                amt = h["value"]
                cash += amt - sell_fee(amt, c, slip)
                trades.append((h["buy_i"], i, h["value"] / h["buy_val"] - 1))
                del hold[c]
        # 买入：补足到 TOP
        while len(hold) < TOP:
            cand = row[~row.index.isin(hold)]
            cand = cand[cand["total_new"] >= THRESHOLD]
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
            hold[best] = {"value": invest, "buy_val": invest, "buy_i": i}
        # 轮动：持仓未满2 且 len>0，新 top 超最弱 X 分则换（此处固定不设 X，X=None 即不做）
        total_now = cash + sum(h["value"] for h in hold.values())
        daily_ret.append(total_now / prev_total - 1 if prev_total > 0 else 0.0)
        prev_total = total_now
    # 期末清仓（不计费，仅补 trades）
    for c, h in hold.items():
        trades.append((h["buy_i"], M, h["value"] / h["buy_val"] - 1))
    return trades, pd.Series(daily_ret)


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
    dates, per_day, market_daily = build_per_day()
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 持仓{TOP} | 止损{STOP:.0%}/止盈{TP:.0%}")
    print("[2/3] 含成本模拟（持有期 × 滑点）...")
    rows = []
    for N in N_LIST:
        for slip in SLIPS:
            trades, daily_ret = simulate(dates, per_day, N, slip)
            s = stats(trades, daily_ret, market_daily)
            rows.append({"N": N, "slip": slip, **s})
            print(f"    N={N} 滑点{slip:.1%}: 超额{s['daily_excess']:.3%} 胜率{s['win_rate']:.1%} "
                  f"盈亏比{s['profit_loss_ratio']:.2f} 回撤{s['max_drawdown']:.1%} 交易{s['n_trades']}")
    res = pd.DataFrame(rows)
    print("[3/3] 保存报告 ...")
    lines = [
        "# 含真实交易成本的轮动回测",
        "",
        f"> 测试期 {START} ~ {END} | 前10池+红线{THRESHOLD}+top{TOP} | 成本：佣金万2(双边,最低5元) + 印花税万5(卖出) + 过户费万0.1(沪市) | 滑点敏感性 0/0.1%/0.2%",
        "",
        "| 持有期 | 滑点/边 | 交易数 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['N']}天 | {r['slip']:.1%} | {r['n_trades']} | {r['win_rate']:.1%} | {r['profit_loss_ratio']:.2f} "
            f"| {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
