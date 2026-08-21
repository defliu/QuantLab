# coding: utf-8
"""新评分体系回测：当日 PE 口径 F6 + 红线 58 + 持仓数扫描 + 一票否决对比。

与实盘一致的口径（复刻 deploy + review_full）：
  每日全市场模型打分 → 模型前 PRE_POOL(100) 池 → 池内 new_total>=58 →
  top-K by prob；池内不足 K 只则当日空仓（宁缺毋滥）。

新评分体系（审计修复后）：
  - F1/F2/F3/F4/F5 沿用评分卡（面板 asof 特征）
  - F6 用【当日 pe_ttm + 当日换手】打分（score_f6，PE<0 或 >100 得 1 分），替代面板 asof SC_F6
  - 规则 A：无否决（F6=1 允许，靠其他维度入围）
  - 规则 B：一票否决（F6=1 即 PE<0 或 >100 的票不可买，宁缺毋滥）

用法：python scan_newsc.py
输出：data/scan_newsc_report.json / .md
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
OUT_JSON = os.path.join(DC.DATA_DIR, "scan_newsc_report.json")
OUT_MD = os.path.join(DC.DATA_DIR, "scan_newsc_report.md")

TOP_KS = [1, 2, 3]
RULES = ["A_无否决", "B_一票否决"]
THRESHOLD = 58.0
PRE_POOL = 100
START, END = "2024-07-01", "2026-08-14"

W = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def main():
    print("[1/4] 加载 v3 面板/模型/特征 + 主库当日估值 ...")
    panel = pd.read_parquet(PANEL)
    panel["trade_date"] = pd.to_datetime(panel["trade_date"])
    meta = json.load(open(META, encoding="utf-8"))
    feat_cols = meta["feature_cols"]
    booster = lgb.Booster(model_file=MODEL)

    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"])
    daily = daily.reset_index()  # trade_date, ts_code, pe_ttm, turnover_rate
    daily = daily.set_index(["trade_date", "ts_code"])

    dates = sorted(panel.loc[(panel["trade_date"] >= START) & (panel["trade_date"] <= END), "trade_date"].unique())
    print(f"    测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日 | 池={PRE_POOL} | 红线 {THRESHOLD}")

    print("[2/4] 逐日打分（新 F6 当日 PE 口径）...")
    market_avgs = []
    per_day = {}
    for i, d in enumerate(dates):
        day = panel[panel["trade_date"] == d].copy()
        if len(day) < 20:
            continue
        X = day[feat_cols].astype("float32").values
        day["prob"] = booster.predict(X)
        sc = DP.compute_scorecard(day)
        for k in ("F1", "F2", "F3", "F4", "F5"):
            day[k] = sc[k].values
        # 合并当日估值（新 F6 口径）
        idx = pd.MultiIndex.from_arrays([day["trade_date"], day["ts_code"]])
        est = daily.reindex(idx)
        day["pe_ttm"] = est["pe_ttm"].values
        day["turnover_rate"] = est["turnover_rate"].values
        day["F6_new"] = [RF.score_f6(p, t) for p, t in zip(day["pe_ttm"], day["turnover_rate"])]
        day["total_new"] = sum(W[k] * day[k] for k in ("F1", "F2", "F3", "F4", "F5")).values * 10.0 \
            + W["F6"] * day["F6_new"] * 10.0
        pre = day.nlargest(PRE_POOL, "prob").copy()
        market_avgs.append(float(day["fwd_ret"].mean()))
        per_day[d] = pre
        if i % 100 == 0:
            print(f"    {i}/{len(dates)} ...")

    print("[3/4] 统计（持仓数 × 否决规则）...")
    market_daily = pd.Series(market_avgs)
    rows = []
    for k in TOP_KS:
        for rule in RULES:
            samples, daily_ret = [], []
            trade_days = flat_days = 0
            for d in dates:
                pre = per_day.get(d)
                if pre is None:
                    continue
                pool = pre[pre["total_new"] >= THRESHOLD]
                if rule.startswith("B"):
                    pool = pool[pool["F6_new"] >= 2]  # 一票否决：PE<0 或 >100 不可买
                if len(pool) < k:
                    flat_days += 1
                    daily_ret.append(0.0)
                    continue
                top = pool.nlargest(k, "prob")
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
                "top_k": k, "rule": rule, "trade_days": trade_days, "flat_days": flat_days,
                "n_samples": len(fwd), "win_rate": win, "profit_loss_ratio": pr,
                "annual_return": ann, "max_drawdown": mdd, "daily_excess": excess,
            })
            print(f"    k={k} {rule}: 胜率{win:.1%} 盈亏比{pr:.2f} 超额{excess:.3%} 空仓{flat_days}天")

    res = pd.DataFrame(rows)
    print("[4/4] 保存报告 ...")
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    data = {"period": [START, END], "pre_pool": PRE_POOL, "threshold": THRESHOLD,
            "note": "新评分体系(当日PE口径F6)+红线58+模型前100池+不足空仓；理想化模拟(T+1收盘买入持有1日、无成本)",
            "results": rows}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    lines = [
        "# 新评分体系回测（当日 PE 口径 F6 + 红线 58）",
        "",
        f"> 测试期 {START} ~ {END} | v3 模型 | 预选池 {PRE_POOL} | 红线 {THRESHOLD} | 理想化模拟",
        "",
        "| 持仓数 | 规则 | 交易天 | 空仓天 | 样本 | 胜率 | 盈亏比 | 年化 | 最大回撤 | 日均超额 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res.to_dict("records"):
        lines.append(
            f"| {r['top_k']} | {r['rule']} | {r['trade_days']} | {r['flat_days']} | {r['n_samples']} "
            f"| {r['win_rate']:.1%} | {r['profit_loss_ratio']:.2f} "
            f"| {r['annual_return']:.1%} | {r['max_drawdown']:.1%} | {r['daily_excess']:.3%} |"
        )
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    报告:", OUT_MD)


if __name__ == "__main__":
    main()
