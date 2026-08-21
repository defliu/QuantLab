# coding: utf-8
"""持仓数量扫描回测：对比 Top1~Top6（等权持仓）的胜率/盈亏比/年化/回撤/超额。

复用 backtest_dual 的逐日双轨选股逻辑，但一次推理同时评估多个 top-k。
口径与 backtest_dual 一致：逐样本算胜率/盈亏比；每日等权组合收益算净值/年化/回撤/超额。
贴近实际策略：v3 模型 + 评分卡红线 65 + 等权（均分）持仓。

用法：
  python scan_topk.py
输出：
  data/scan_topk_report.json / .md
"""
import json
import os

import numpy as np
import pandas as pd
import lightgbm as lgb

import data_config as DC
import deploy_predict as DP

HERE = DC.PROJECT_DIR
PANEL = os.path.join(DC.DATA_DIR, "feature_panel_v3.parquet")
MODEL = DC.model_file("_v3")
META = os.path.join(DC.DATA_DIR, "features_v3.json")
OUT_JSON = os.path.join(DC.DATA_DIR, "scan_topk_report.json")
OUT_MD = os.path.join(DC.DATA_DIR, "scan_topk_report.md")

TOP_KS = [1, 2, 3, 4, 5, 6]
THRESHOLD = 65.0
START, END = "2024-07-01", "2026-08-14"


def main():
    print("[1/4] 加载 v3 面板/模型/特征 ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)
    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 交易日 | 红线 {THRESHOLD}")

    print("[2/4] 逐日双轨打分（一次推理，评估多个 top-k）...")
    samples = {k: [] for k in TOP_KS}     # 逐样本 fwd（胜率/盈亏比）
    daily_ret = {k: [] for k in TOP_KS}   # 每日等权组合收益（净值/年化/回撤/超额）
    market_avgs = []
    for i, d in enumerate(dates):
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        X = day[feat_cols].astype("float32").values
        day["prob"] = booster.predict(X)
        day["sc_total"] = DP.compute_scorecard(day)["total"].values
        market_avgs.append(float(day["fwd_ret"].mean()))
        pool = day[day["sc_total"] >= THRESHOLD]
        if len(pool) < max(TOP_KS):
            pool = day  # 池内不足则放宽（避免空仓）
        for k in TOP_KS:
            top = pool.nlargest(k, "prob")
            samples[k].extend(top["fwd_ret"].tolist())
            daily_ret[k].append(float(top["fwd_ret"].mean()))
        if i % 100 == 0:
            print(f"    {i}/{len(dates)} ...")

    print("[3/4] 统计各 top-k 指标 ...")
    market_daily = pd.Series(market_avgs)
    rows = []
    for k in TOP_KS:
        fwd = pd.Series(samples[k])
        win = float((fwd > 0).mean())
        aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
        al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
        pr = float(aw / abs(al)) if al and al != 0 else np.nan
        sel = pd.Series(daily_ret[k])
        nav = (1 + sel).cumprod()
        n = len(sel)
        ann = float(nav.iloc[-1] ** (252 / n) - 1) if n > 0 else np.nan
        mdd = float((nav / nav.cummax() - 1).min())
        excess = float(sel.mean() - market_daily.mean())
        rows.append({
            "top_k": k, "n_samples": len(fwd), "n_days": n,
            "win_rate": win, "avg_win": aw, "avg_loss": al, "profit_loss_ratio": pr,
            "annual_return": ann, "max_drawdown": mdd, "daily_excess": excess,
        })

    res = pd.DataFrame(rows)
    print(res.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    print("[4/4] 保存报告 ...")
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    data = {"period": [START, END], "threshold": THRESHOLD,
            "note": "v3模型+评分卡65红线+等权持仓；理想化模拟(T+1收盘买入持有1日、无成本)", "results": rows}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    lines = [
        "# 持仓数量扫描回测（Top1~Top6）",
        "",
        f"> 测试期 {START} ~ {END} | v3 模型 + 评分卡红线 {THRESHOLD} | 等权（均分）持仓 | 理想化模拟",
        "",
        "| 持仓数 | 样本 | 胜率 | 盈亏比 | 年化 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['top_k']} | {r['n_samples']} | {r['win_rate']:.1%} | {r['profit_loss_ratio']:.2f} "
            f"| {r['annual_return']:.1%} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
