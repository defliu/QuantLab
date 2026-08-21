# coding: utf-8
"""评分卡红线扫描（模型预选池内口径）：完全复刻 deploy_predict 流程。

流程（与实盘一致）：
  每日全市场打分 → 取模型概率前 PRE_POOL(100) 只预选池 → 池内过滤 sc_total>=红线 →
  取 prob 前 TOP_K(2) 只；若池内过线不足 K 只则当日空仓（宁缺毋滥，与实际买入逻辑一致）。

目的：确认 65 红线在"模型前100池内"口径下是否过严（对比 scan_threshold.py 的全市场口径）。

用法：
  python scan_threshold_pool.py
输出：
  data/scan_threshold_pool_report.json / .md
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
OUT_JSON = os.path.join(DC.DATA_DIR, "scan_threshold_pool_report.json")
OUT_MD = os.path.join(DC.DATA_DIR, "scan_threshold_pool_report.md")

TOP_K = 2
PRE_POOL = 100
THRESHOLDS = [50, 55, 58, 60, 62, 65]
START, END = "2024-07-01", "2026-08-14"


def main():
    print("[1/4] 加载 v3 面板/模型/特征 ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)
    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 预选池={PRE_POOL} | TOP_K={TOP_K}")

    print("[2/4] 逐日双轨打分（模型前100池，多阈值评估）...")
    market_avgs = []
    per_day = {}
    for i, d in enumerate(dates):
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        X = day[feat_cols].astype("float32").values
        day["prob"] = booster.predict(X)
        day["sc_total"] = DP.compute_scorecard(day)["total"].values
        pre = day.nlargest(PRE_POOL, "prob").copy()
        market_avgs.append(float(day["fwd_ret"].mean()))
        per_day[d] = pre
        if i % 100 == 0:
            print(f"    {i}/{len(dates)} ...")

    print("[3/4] 统计各阈值指标 ...")
    market_daily = pd.Series(market_avgs)
    rows = []
    for thr in THRESHOLDS:
        samples = []
        daily_ret = []
        trade_days = 0
        flat_days = 0
        for d in dates:
            pre = per_day.get(d)
            if pre is None:
                continue
            pool = pre[pre["sc_total"] >= thr]
            if len(pool) < TOP_K:
                flat_days += 1
                daily_ret.append(0.0)
                continue
            top = pool.nlargest(TOP_K, "prob")
            samples.extend(top["fwd_ret"].tolist())
            daily_ret.append(float(top["fwd_ret"].mean()))
            trade_days += 1
        fwd = pd.Series(samples)
        win = float((fwd > 0).mean()) if len(fwd) else np.nan
        aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
        al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
        pr = float(aw / abs(al)) if al and al != 0 else np.nan
        sel = pd.Series(daily_ret)
        nav = (1 + sel).cumprod()
        n = len(sel)
        ann = float(nav.iloc[-1] ** (252 / n) - 1) if n > 0 and nav.iloc[-1] > 0 else np.nan
        mdd = float((nav / nav.cummax() - 1).min()) if n else np.nan
        excess = float(sel.mean() - market_daily.mean())
        rows.append({
            "threshold": thr, "trade_days": trade_days, "flat_days": flat_days, "n_samples": len(fwd),
            "win_rate": win, "profit_loss_ratio": pr, "annual_return": ann,
            "max_drawdown": mdd, "daily_excess": excess,
        })

    res = pd.DataFrame(rows)
    print(res.to_string(index=False, float_format=lambda x: f"{x:.4f}"))

    print("[4/4] 保存报告 ...")
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    data = {"period": [START, END], "top_k": TOP_K, "pre_pool": PRE_POOL,
            "note": "v3模型+模型前100池内过滤红线+不足K只空仓+等权TOP2；复刻 deploy 实盘流程；理想化模拟", "results": rows}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    lines = [
        "# 评分卡红线扫描（模型前100池内，复刻实盘）",
        "",
        f"> 测试期 {START} ~ {END} | v3 模型 | 预选池 {PRE_POOL} | 持仓 {TOP_K} 只 | 严格红线（池内不足 K 只则空仓）",
        "",
        "| 红线 | 交易天 | 空仓天 | 样本 | 胜率 | 盈亏比 | 年化 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['threshold']} | {r['trade_days']} | {r['flat_days']} | {r['n_samples']} "
            f"| {r['win_rate']:.1%} | {r['profit_loss_ratio']:.2f} "
            f"| {r['annual_return']:.1%} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
