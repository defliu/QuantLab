# coding: utf-8
"""「触价 +5% 即换票」第二步：组合层轮动回测（TP × N × 出场模式 矩阵，T-20260911-001）。

第一步（scan_mfe_tp5.py）是逐笔无资金约束口径；本脚本用官方引擎 scan_rotate_cost_real
跑组合层（10万本金 / 持仓2只 / 资金约束 / 轮动再部署 / 真实费用），回答：
  - 触价止盈（touch_tp：盘中高点≥成本×(1+TP) 即卖，跳空高开按开盘成交）
  - vs 开盘口径止盈（fixed：开盘价≥TP 才卖）
  - vs 现行实盘口径（live_trail TP15）
在 TP{5%,15%} × N{3,5,10} 上的真实日均超额 / 回撤 / Calmar / 平均持有期。

基线锚点（止盈追盈优先级敏感性_20260908.md，同面板同口径）：红线60/滑点0.1%：
  fixed_TP15_N10 日超额 +0.015% / 回撤 -30.8%；live_TP15_N10 +0.132% / -20.9% / Calmar 1.60。
本脚本基线组应精确复现上述数字（引擎改动零影响的验证）。

判据（防过拟合纪律）：
  1) 分年稳健：2026 YTD 不转负（第一步逐笔口径 2026 已转负，组合层需复核）
  2) T-20260907-002 防复发线：平均持有期被显著压缩的出场规则 = 负优化先验
  3) 与基线 live_TP15_N10 的日均超额/Calmar 对照，而非只看单格最优

用法: python run_tp5_rotation.py
输出: results/TP5短持有期轮动回测_20260911.md
"""
import datetime
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats as sps

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import scan_rotate_cost_real as eng  # noqa: E402

RESULTS = os.path.join(PROJ, "results")
OUT_MD = os.path.join(RESULTS, "TP5短持有期轮动回测_20260911.md")

# 组：名称 -> (EXIT_MODE, TP, N)
GROUPS = [
    ("fixed_TP15_N10", "fixed", 0.15, 10),      # 官方回测基线（对齐 20260908 报告）
    ("live_TP15_N10", "live_trail", 0.15, 10),  # 现行实盘口径基线
    ("fixed_TP15_N3", "fixed", 0.15, 3),        # 纯持有期效应对照
    ("fixed_TP15_N5", "fixed", 0.15, 5),
    ("fixed_TP5_N10", "fixed", 0.05, 10),       # 开盘口径 TP5
    ("fixed_TP5_N5", "fixed", 0.05, 5),
    ("fixed_TP5_N3", "fixed", 0.05, 3),
    ("live_TP5_N5", "live_trail", 0.05, 5),
    ("live_TP5_N3", "live_trail", 0.05, 3),
    ("touch_TP5_N10", "touch_tp", 0.05, 10),    # 触价口径 TP5（本研究的核心格）
    ("touch_TP5_N5", "touch_tp", 0.05, 5),
    ("touch_TP5_N3", "touch_tp", 0.05, 3),
]
THRESHOLDS = [60.0, 58.0]
SLIP = 0.001

# 基线锚点（20260908 报告，用于验证引擎改动零影响）
ANCHOR = {("fixed_TP15_N10", 60.0): 0.00015, ("live_TP15_N10", 60.0): 0.00132,
          ("fixed_TP15_N10", 58.0): -0.00003, ("live_TP15_N10", 58.0): 0.00147}


def avg_hold(trades):
    if not trades:
        return np.nan
    return float(np.mean([t[1] - t[0] - 1 for t in trades]))


def yearly_excess(dates, daily_ret, market_daily):
    """分年日均超额（防过拟合判据1：2026 YTD 是否转负）。"""
    df = pd.DataFrame({"d": pd.to_datetime(dates), "ret": daily_ret.values, "mkt": market_daily.values})
    df["year"] = df["d"].dt.year
    out = {}
    for y, g in df.groupby("year"):
        out[int(y)] = float((g["ret"] - g["mkt"]).mean())
    return out


def main():
    os.makedirs(RESULTS, exist_ok=True)
    print("加载数据（一次）...", flush=True)
    dates, per_day, market_daily, open_map = eng.build_per_day()
    print(f"  测试期 {dates[0].date()} ~ {dates[-1].date()} | {len(dates)} 日", flush=True)
    all_rows = []
    for thr in THRESHOLDS:
        eng.THRESHOLD = thr
        for name, mode, tp, n in GROUPS:
            eng.EXIT_MODE = mode
            eng.TP = tp
            eng.EXIT_REASON_CNT.clear()
            eng.SELL_SKIP_DOWN[0] = 0
            eng.SELL_DELIST[0] = 0
            trades, daily_ret, n_skip = eng.simulate(dates, per_day, n, SLIP, open_map, exec_ok=True)
            s = eng.stats(trades, daily_ret, market_daily)
            reasons = " ".join("%s=%d" % (k, v) for k, v in sorted(eng.EXIT_REASON_CNT.items()))
            ann = s["daily_excess"] * 252
            calmar = (ann / 100.0) / abs(s["max_drawdown"] / 100.0) if s["max_drawdown"] else 0.0
            yr = yearly_excess(dates, daily_ret, market_daily)
            row = {"group": name, "thr": int(thr), "mode": mode, "tp": tp, "N": n,
                   "ex": s["daily_excess"], "ann": ann, "mdd": s["max_drawdown"], "calmar": calmar,
                   "win": s["win_rate"], "pl": s["profit_loss_ratio"], "n": s["n_trades"],
                   "hold": avg_hold(trades),
                   "ex24": yr.get(2024), "ex25": yr.get(2025), "ex26": yr.get(2026),
                   "daily_ret": daily_ret.copy(),
                   "reasons": reasons}
            all_rows.append(row)
            print("[%s thr%d] %s TP=%s N=%d: 超额%+.3f%% 年化%+.1f%% 回撤%.1f%% Calmar%.2f 胜率%.1f%% "
                  "持有%.1f日 分年%s | %s"
                  % (name, int(thr), mode, tp, n, s["daily_excess"] * 100, ann * 100,
                     s["max_drawdown"] * 100, calmar, s["win_rate"] * 100, row["hold"],
                     {k: round(v * 100, 3) for k, v in yr.items()}, reasons), flush=True)

    # ---- 基线锚点核对（引擎 touch_tp 改动零影响验证）----
    anchor_ok = []
    for (g, thr), v in ANCHOR.items():
        got = [r for r in all_rows if r["group"] == g and float(r["thr"]) == thr][0]["ex"]
        ok = abs(got - v) <= 0.0002
        anchor_ok.append((g, thr, v, got, ok))
        print(f"[锚点] {g} thr{int(thr)}: 期望{v * 100:+.3f}% 实测{got * 100:+.3f}% => {'PASS' if ok else 'FAIL'}",
              flush=True)

    # ---- 配对 t 检验：各组日收益 vs 同红线基线 live_TP15_N10（统计纪律：不显著不作判定）----
    for thr in THRESHOLDS:
        rows_thr = [r for r in all_rows if float(r["thr"]) == thr]
        base = [r for r in rows_thr if r["group"] == "live_TP15_N10"][0]
        for r in rows_thr:
            if r["group"] == "live_TP15_N10":
                r["t_vs_base"], r["p_vs_base"] = np.nan, np.nan
                continue
            t, p = sps.ttest_rel(r["daily_ret"].values, base["daily_ret"].values)
            r["t_vs_base"], r["p_vs_base"] = float(t), float(p)
            print(f"[配对t] thr{int(thr)} {r['group']} vs live_TP15_N10: t={t:+.2f} p={p:.3f}", flush=True)

    # ---- 报告 ----
    today = datetime.date.today().strftime("%Y%m%d")
    lines = [
        "# 触价 +5% 短持有期轮动回测（" + today + "）",
        "",
        f"> T-20260911-001 第二步。引擎 `scan_rotate_cost_real.simulate(exec_ok)` 官方可执行口径："
        f"10万本金/持仓{eng.TOP}只/红线阈值/止损-7%/滑点0.1%/真实费率/一字板与停牌过滤。",
        f"> 测试期 {dates[0].date()} ~ {dates[-1].date()}（{len(dates)} 日），候选流与第一步相同"
        f"（v3_sc 面板 + v3_enh 模型 + 真实评分卡）。",
        "> **单源声明：gpsj 备用源不可用，本报告结论仅基于 astock，未经备用源交叉验证。**",
        "",
        "> 出场模式：`fixed`=开盘价触发止损/止盈；`live_trail`=-7%止损+8%追盈(激活+8%+保本底线)+止盈；"
        "`touch_tp`=开盘-7%止损+**盘中高点**≥成本×(1+TP) 触价卖出（跳空高开按开盘成交，T+1 锁后 T+2 起）。",
        "> 平均持有=决策日到出场日的交易日数-1（买入日 T+1 记 0）。分年超额=该年（日收益-同日全市场均值）的日均值。",
        "",
        "## 基线锚点核对（引擎改动零影响验证）",
        "",
        "| 组 | 红线 | 20260908 报告值 | 本次实测 | 结论 |",
        "|---|---|---|---|---|",
    ]
    for g, thr, v, got, ok in anchor_ok:
        lines.append(f"| {g} | {int(thr)} | {v * 100:+.3f}% | {got * 100:+.3f}% | {'PASS' if ok else 'FAIL'} |")

    lines += [
        "",
        "## 全量矩阵",
        "",
    ]
    for thr in THRESHOLDS:
        lines += [
            f"### 红线 {int(thr)}",
            "",
            "| 组 | 模式 | TP | N | 日均超额 | 年化超额 | 最大回撤 | Calmar | 胜率 | 盈亏比 | 交易数 | 平均持有(日) | 分年超额(24/25/26) | t vs基线 | p | 出场原因 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in all_rows:
            if float(r["thr"]) != thr:
                continue
            yr_str = "/".join("—" if r[k] is None else f"{r[k] * 100:+.2f}%" for k in ("ex24", "ex25", "ex26"))
            t_str = "基线" if np.isnan(r.get("t_vs_base", np.nan)) else f"{r['t_vs_base']:+.2f}"
            p_str = "—" if np.isnan(r.get("p_vs_base", np.nan)) else f"{r['p_vs_base']:.3f}"
            lines.append(
                f"| {r['group']} | {r['mode']} | {r['tp']:.0%} | {r['N']} | {r['ex'] * 100:+.3f}% "
                f"| {r['ann'] * 100:+.1f}% | {r['mdd'] * 100:.1f}% | {r['calmar']:.2f} | {r['win'] * 100:.1f}% "
                f"| {r['pl']:.2f} | {r['n']} | {r['hold']:.1f} | {yr_str} | {t_str} | {p_str} | {r['reasons']} |")
        lines.append("")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("报告 ->", OUT_MD)
    return 0


if __name__ == "__main__":
    sys.exit(main())
