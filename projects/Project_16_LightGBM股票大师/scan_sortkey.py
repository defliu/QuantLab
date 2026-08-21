# coding: utf-8
"""排序键对照回测：实盘按 total 还是 prob 选股？

口径（贴近 deploy_predict 实盘）：
  每日全市场模型打分 → 模型前100池 → 取 prob 前10（模拟 model_top10 候选清单）→
  池内 total_new>=红线58 过滤 → 取前 TOP_K(2) 只，按不同排序键：
    A = prob（模型概率）
    B = total_new（评分卡总分，当前实盘用）
  每日换仓（T+1 收盘买入持有1日、无成本），对比胜率/盈亏比/回撤/超额。

用法：python scan_sortkey.py
输出：data/scan_sortkey_report.md
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
OUT_MD = os.path.join(DC.DATA_DIR, "scan_sortkey_report.md")

THRESHOLD = 58.0
PRE_POOL = 100
TOP10 = 10
TOP_K = 2
START, END = "2024-07-01", "2026-08-14"
W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def main():
    print("[1/3] 加载 + 逐日打分 ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily = daily.set_index(["trade_date", "ts_code"])

    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    market_avgs, top10_probs, top10_totals = [], [], []
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
        pre = day.nlargest(PRE_POOL, "prob")
        top10 = pre.nlargest(TOP10, "prob")
        market_avgs.append(float(day["fwd_ret"].mean()))
        pool = top10[top10["total_new"] >= THRESHOLD]
        a = pool.nlargest(TOP_K, "prob")
        b = pool.nlargest(TOP_K, "total_new")
        top10_probs.append(float(a["fwd_ret"].mean()) if len(a) else 0.0)
        top10_totals.append(float(b["fwd_ret"].mean()) if len(b) else 0.0)

    print("[2/3] 统计 ...")
    market_daily = pd.Series(market_avgs)
    rows = []
    for name, seq in [("A_prob排序", top10_probs), ("B_total排序(当前实盘)", top10_totals)]:
        sel = pd.Series(seq)
        fwd = pd.Series([r for r in seq if r != 0.0])
        win = float((fwd > 0).mean()) if len(fwd) else np.nan
        aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
        al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
        pr = float(aw / abs(al)) if al and al != 0 else np.nan
        nav = (1 + sel).cumprod()
        mdd = float((nav / nav.cummax() - 1).min())
        excess = float(sel.mean() - market_daily.mean())
        rows.append({"key": name, "win_rate": win, "profit_loss_ratio": pr,
                     "max_drawdown": mdd, "daily_excess": excess})
        print(f"    {name}: 胜率{win:.1%} 盈亏比{pr:.2f} 回撤{mdd:.1%} 超额{excess:.3%}")

    print("[3/3] 保存报告 ...")
    lines = [
        "# 排序键对照（deploy 前10池口径：total vs prob）",
        "",
        f"> 测试期 {START} ~ {END} | v3模型 + 新评分(当日PE F6) | 模型前100池→前10池(prob) → 红线{THRESHOLD} → top{TOP_K} | 每日换仓 | 理想化模拟",
        "",
        "| 排序键 | 胜率 | 盈亏比 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['key']} | {r['win_rate']:.1%} | {r['profit_loss_ratio']:.2f} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
