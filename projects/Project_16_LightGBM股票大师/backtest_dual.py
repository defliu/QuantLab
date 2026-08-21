# coding: utf-8
"""双轨选股样本外回测：验证选股体系的胜率 / 盈亏比 / 超额 / 回撤。

逻辑（简化理想化模拟，用于验证信号排序能力）：
  对测试期每个交易日：
    1) 取当日全市场特征（feature_panel_v2.parquet 当日行）
    2) 用训练好的 v2 模型推理"次日上涨概率"
    3) 双轨筛选：评分卡总分 ≥ 阈值 → 按模型概率取 TopK
    4) 记录这 TopK 的次日实际收益（fwd_ret）

统计指标：
  - 每日 TopK 平均收益 vs 全市场平均收益（超额）
  - 胜率、平均盈/亏、盈亏比
  - 等权每日换仓净值：年化、最大回撤

假设（理想化）：T+1 次日按收盘买入持有 1 日、无成本、无涨跌停成交限制。
目的：验证"模型 + 评分卡"的排序有效性，非精确实盘回测。

用法：
  python backtest_dual.py --panel data/feature_panel_v2.parquet --model D:/QuantLab/models/lgb_model_v2.txt
  python backtest_dual.py --top-k 5 --threshold 60 --start 2024-07-01 --end 2026-08-14
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP  # 复用评分卡

HERE = DC.PROJECT_DIR
OUT = os.path.join(DC.DATA_DIR, "backtest_dual_report.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=os.path.join(DC.DATA_DIR, "feature_panel_v2.parquet"))
    ap.add_argument("--model", default=DC.model_file("_v2"))
    ap.add_argument("--meta", default=os.path.join(DC.DATA_DIR, "features_v2.json"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--threshold", type=float, default=60.0, help="评分卡红线(池内)")
    ap.add_argument("--start", default="2024-07-01")
    ap.add_argument("--end", default="2026-08-14")
    args = ap.parse_args()

    print("[1/4] 加载面板与模型 ...")
    panel = pd.read_parquet(args.panel)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(args.meta, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=args.model)
    dates = sorted(panel.loc[(panel["trade_date"] >= args.start) & (panel["trade_date"] <= args.end), "trade_date"].unique())
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 交易日 | Top{args.top_k} | 红线 {args.threshold}")

    print("[2/4] 逐日双轨选股 + 记录次日收益 ...")
    rows = []
    for i, d in enumerate(dates):
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        X = day[feat_cols].astype("float32").values
        day["prob"] = booster.predict(X)
        sc = DP.compute_scorecard(day)
        day["sc_total"] = sc["total"].values
        pool = day[day["sc_total"] >= args.threshold]
        if len(pool) < args.top_k:  # 池内不足则放宽容许（避免空仓，标注"放宽"）
            pool = day
        top = pool.nlargest(args.top_k, "prob")
        for _, r in top.iterrows():
            rows.append({"date": d, "code": r["ts_code"], "prob": float(r["prob"]),
                         "sc": float(r["sc_total"]), "fwd": float(r["fwd_ret"])})
    res = pd.DataFrame(rows)
    print(f"    选中记录 {len(res):,} 条")

    print("[3/4] 统计胜率 / 盈亏比 / 超额 / 回撤 ...")
    sel_daily = res.groupby("date")["fwd"].mean()
    market_avg = panel[panel["trade_date"].isin(dates)].groupby("trade_date")["fwd_ret"].mean()
    market_avg = market_avg.reindex(sel_daily.index)
    excess = sel_daily - market_avg

    win_rate = float((res["fwd"] > 0).mean())
    avg_win = float(res.loc[res["fwd"] > 0, "fwd"].mean())
    avg_loss = float(res.loc[res["fwd"] < 0, "fwd"].mean())
    profit_ratio = float(avg_win / abs(avg_loss)) if avg_loss != 0 else np.nan
    nav = (1 + sel_daily).cumprod()
    n_days = len(sel_daily)
    ann = float(nav.iloc[-1] ** (252 / n_days) - 1) if n_days > 0 else np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    market_nav = (1 + market_avg).cumprod()
    market_ann = float(market_nav.iloc[-1] ** (252 / n_days) - 1) if n_days > 0 else np.nan

    stats = {
        "period": [str(dates[0].date()), str(dates[-1].date())],
        "n_days": n_days,
        "top_k": args.top_k, "threshold": args.threshold,
        "daily_ret_mean": float(sel_daily.mean()),
        "market_daily_ret_mean": float(market_avg.mean()),
        "daily_excess": float(excess.mean()),
        "win_rate": win_rate,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_loss_ratio": profit_ratio,
        "annual_return": ann,
        "market_annual_return": market_ann,
        "max_drawdown": mdd,
        "note": "理想化模拟：T+1收盘买入持有1日、无成本、无涨跌停限制；用于验证信号排序有效性",
    }
    for k, v in stats.items():
        print(f"    {k:<22} {v:.6f}" if isinstance(v, float) else f"    {k:<22} {v}")

    print("[4/4] 保存报告 ...")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2, default=str)
    print("    报告:", OUT)


if __name__ == "__main__":
    main()
