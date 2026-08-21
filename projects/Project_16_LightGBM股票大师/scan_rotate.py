# coding: utf-8
"""轮动/换仓回测：新票分高过仓内持仓时，该不该换？

回答"持仓没触发卖点、但新票分更高怎么办"：
  口径 = 模型前100池 + 新评分体系(当日PE F6) + 红线58 + 持仓2只 + 止损-7%/止盈+15%(累计模拟)
  参数：
    N  = 持有期（天数），到期换仓：1 / 3 / 5 / 10
    X  = 轮动阈值：None(不轮动，只到期/风控换)；8 / 12（期间新票 top1 分 - 持仓最弱分 > X 则提前替换）
  收益路径用逐日 fwd_ret 串联；止损止盈用累计收益模拟。

用法：python scan_rotate.py
输出：data/scan_rotate_report.md
"""
import json
import os

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP
import review_full as RF

HERE = DC.PROJECT_DIR
PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
MODEL = DC.model_file("_v3")
META = os.path.join(DC.DATA_DIR, "features_v3.json")
OUT_MD = os.path.join(DC.DATA_DIR, "scan_rotate_report.md")

THRESHOLD = 58.0
PRE_POOL = 100
TOP10 = 10
TOP = 2
STOP, TP = -0.07, 0.15
N_LIST = [1, 3, 5, 10]
X_LIST = [None, 8, 12]
START, END = "2024-07-01", "2026-08-14"

W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def build_per_day():
    print("[1/3] 加载面板/模型 + 逐日新体系打分 ...")
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
        market_avgs.append(float(day["fwd_ret"].mean()))  # 全市场基准
        pre = day.nlargest(PRE_POOL, "prob")[["ts_code", "total_new", "fwd_ret", "prob"]].set_index("ts_code")
        per_day[d] = pre.nlargest(TOP10, "prob")  # 实盘口径：模型 prob 前10 候选池
    return dates, per_day, pd.Series(market_avgs)


def simulate(dates, per_day, market_daily, N, X):
    M = len(dates)
    holding, buy_day, cum = [], {}, {}
    trades, daily_ret = [], []
    for i, d in enumerate(dates):
        row = per_day[d]
        if i > 0:
            prev = per_day[dates[i - 1]]
            for c in holding:
                r = float(prev.loc[c, "fwd_ret"]) if c in prev.index else 0.0
                cum[c] *= (1 + r)

        remove = []
        for c in holding:
            age = i - buy_day[c]
            if cum[c] - 1 <= STOP or cum[c] - 1 >= TP or age >= N:
                trades.append((buy_day[c], i, cum[c] - 1))
                remove.append(c)
        for c in remove:
            holding.remove(c)

        if X is not None:
            while len(holding) < TOP and len(holding) > 0:
                pool = row[~row.index.isin(holding)]
                if len(pool) == 0:
                    break
                best = pool.nlargest(1, "total_new").index[0]
                weakest = min(holding, key=lambda c: row.loc[c, "total_new"] if c in row.index else -1e9)
                wscore = row.loc[weakest, "total_new"] if weakest in row.index else -1e9
                if row.loc[best, "total_new"] - wscore > X:
                    trades.append((buy_day[weakest], i, cum[weakest] - 1))
                    holding.remove(weakest)
                    holding.append(best); buy_day[best] = i; cum[best] = 1.0
                else:
                    break

        while len(holding) < TOP:
            cand = row[~row.index.isin(holding)]
            cand = cand[cand["total_new"] >= THRESHOLD]
            if len(cand) == 0:
                break
            best = cand.nlargest(1, "total_new").index[0]
            holding.append(best); buy_day[best] = i; cum[best] = 1.0

        # 决策完成后，用当日持仓的 fwd_ret 作为组合日收益（决策日视角，与市场基准同源对齐）
        present = [c for c in holding if c in row.index]
        daily_ret.append(float(row.loc[present, "fwd_ret"].mean()) if present else 0.0)

    for c in list(holding):
        trades.append((buy_day[c], M, cum[c] - 1))
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
    return {
        "n_trades": len(trades), "win_rate": win, "profit_loss_ratio": pr,
        "max_drawdown": mdd, "daily_excess": excess, "avg_hold": float(np.mean([t[1] - t[0] for t in trades])) if trades else np.nan,
    }


def main():
    dates, per_day, market_daily = build_per_day()
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 持仓 {TOP} | 止损{STOP:.0%}/止盈{TP:.0%}")
    print("[2/3] 模拟各 (持有期N × 轮动阈值X) ...")
    rows = []
    for N in N_LIST:
        for X in X_LIST:
            trades, daily_ret = simulate(dates, per_day, market_daily, N, X)
            s = stats(trades, daily_ret, market_daily)
            rows.append({"N": N, "X": ("不轮动" if X is None else f"+{X}分"), **s})
            print(f"    N={N} X={X}: 胜率{s['win_rate']:.1%} 盈亏比{s['profit_loss_ratio']:.2f} "
                  f"超额{s['daily_excess']:.3%} 回撤{s['max_drawdown']:.1%} 持仓{s['avg_hold']:.1f}天")
    res = pd.DataFrame(rows)
    print("[3/3] 保存报告 ...")
    lines = [
        "# 轮动/换仓回测（新票分高过仓内持仓，要不要换？）",
        "",
        f"> 测试期 {START} ~ {END} | v3模型 + 新评分(当日PE F6) + 红线{THRESHOLD} | 持仓{TOP}只 | 止损{STOP:.0%}/止盈{TP:.0%} | 理想化模拟(无成本)",
        "",
        "| 持有期N | 轮动阈值X | 交易次数 | 平均持有天 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['N']}天 | {r['X']} | {r['n_trades']} | {r['avg_hold']:.1f} "
            f"| {r['win_rate']:.1%} | {r['profit_loss_ratio']:.2f} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
