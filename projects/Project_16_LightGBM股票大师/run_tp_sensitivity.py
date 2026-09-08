# coding: utf-8
"""止盈/追盈优先级敏感性回测驱动（2026-09-08，601999 止盈卖飞案例引发）。

矩阵：EXIT 模式 × 止盈线 × 追盈优先，跑红线58/60 两档，N=10，滑点0.1%（与昨日 ablation 同口径可比）。
以 import + 改模块属性方式复用 scan_rotate_cost_real（build_per_day 只加载一次），汇总 markdown。

用法: python run_tp_sensitivity.py
"""
import os

import scan_rotate_cost_real as eng

PROJ = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(PROJ, "results")

# 组：名称 -> (EXIT_MODE, TP, TRAIL_FIRST)
GROUPS = [
    ("fixed_TP15",              "fixed",      0.15, 0),
    ("live_TP15",               "live_trail", 0.15, 0),
    ("live_TP20",               "live_trail", 0.20, 0),
    ("live_TP25",               "live_trail", 0.25, 0),
    ("live_TP30",               "live_trail", 0.30, 0),
    ("live_pure_trail",         "live_trail", 0.99, 0),
    ("trail_first_TP15",        "live_trail", 0.15, 1),
    ("trail_first_TP20",        "live_trail", 0.20, 1),
    ("trail_first_TP25",        "live_trail", 0.25, 1),
]

THRESHOLDS = [60.0, 58.0]
N = 10
SLIP = 0.001


def main():
    os.makedirs(RESULTS, exist_ok=True)
    print("加载数据（一次）...", flush=True)
    dates, per_day, market_daily, open_map = eng.build_per_day()
    print("  测试期 %s ~ %s | %d 日" % (dates[0].date(), dates[-1].date(), len(dates)), flush=True)
    all_rows = []
    for thr in THRESHOLDS:
        eng.THRESHOLD = thr
        for name, mode, tp, tf in GROUPS:
            eng.EXIT_MODE = mode
            eng.TP = tp
            eng.TRAIL_FIRST = tf
            eng.EXIT_REASON_CNT.clear()
            eng.SELL_SKIP_DOWN[0] = 0
            eng.SELL_DELIST[0] = 0
            trades, daily_ret, n_skip = eng.simulate(dates, per_day, N, SLIP, open_map, exec_ok=True)
            s = eng.stats(trades, daily_ret, market_daily)
            reasons = " ".join("%s=%d" % (k, v) for k, v in sorted(eng.EXIT_REASON_CNT.items()))
            ann = s["daily_excess"] * 252
            calmar = (ann / 100.0) / abs(s["max_drawdown"] / 100.0) if s["max_drawdown"] else 0.0
            row = {"group": name, "thr": int(thr), "mode": mode, "tp": tp, "trail_first": tf,
                   "ex": s["daily_excess"], "ann": ann, "mdd": s["max_drawdown"], "calmar": calmar,
                   "win": s["win_rate"], "pl": s["profit_loss_ratio"], "n": s["n_trades"],
                   "reasons": reasons}
            all_rows.append(row)
            print("[%s thr%d] %s TP=%s tf=%d: 超额%+.3f%% 年化%+.1f%% 回撤%.1f%% Calmar%.2f 胜率%.1f%% 盈亏比%.2f 交易%d | %s"
                  % (name, int(thr), mode, tp, tf, s["daily_excess"] * 100, ann * 100, s["max_drawdown"] * 100,
                     calmar, s["win_rate"] * 100, s["profit_loss_ratio"], s["n_trades"], reasons), flush=True)
    write_report(all_rows)


def write_report(rows):
    lines = [
        "# 止盈线 / 追盈优先级敏感性回测（2026-09-08）",
        "",
        "> 触发背景：601999（出版传媒）09-08 13:30 在 +15.07% 触发固定止盈卖出（7.94），当日后续涨停收 8.02，",
        "> 引出「固定止盈 15% 是否卖飞强势票」之问。本回测对比：止盈线 15/20/25/30 / 纯追盈（TP=99%）/ 追盈优先。",
        "",
        "> 口径：open→open 可执行，N=10，滑点 0.1%/边，止损 -7%，追盈 8%（激活 +8% 修复语义），红线 58/60 两档，与 20260907 ablation 同口径。",
        "> 出场原因：MATURE=持有期满 / TP=固定止盈 / TRAIL=追盈 / STOP=止损。",
        "",
    ]
    for thr in THRESHOLDS:
        lines.append("## 红线 %.0f · N=%d · 滑点0.1%%" % (thr, N))
        lines.append("")
        lines.append("| 组合 | 止盈线 | 追盈优先 | 日均超额 | 年化超额 | 最大回撤 | Calmar | 胜率 | 盈亏比 | 交易数 | 出场原因 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for r in rows:
            if r["thr"] != int(thr):
                continue
            tp_label = "99%(纯追盈)" if r["tp"] >= 0.9 else "%.0f%%" % (r["tp"] * 100)
            tf_label = "是" if r["trail_first"] else "否"
            lines.append("| %s | %s | %s | %+.3f%% | %+.1f%% | %.1f%% | %.2f | %.1f%% | %.2f | %d | %s |"
                         % (r["group"], tp_label, tf_label, r["ex"] * 100, r["ann"] * 100, r["mdd"] * 100,
                            r["calmar"], r["win"] * 100, r["pl"], r["n"], r["reasons"]))
        lines.append("")
    out = os.path.join(RESULTS, "止盈追盈优先级敏感性_20260908.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("报告:", out)


if __name__ == "__main__":
    main()
