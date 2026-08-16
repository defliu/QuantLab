# coding: utf-8
"""T-20260815-001 报告生成：10万/8只+价<50 上加 vol_target 无杠杆扫描对比。

读 reports/voltarget_10w_price50_scan.json（scan_10w_price50_voltarget.py 产物），
输出：业绩指标表 + 分年度 + 平均持仓/资金利用率 → 写 results/voltarget扫描报告_20260815.md。
"""
import json
import os

import pandas as pd

ROOT = "D:/QuantLab"
SCAN_JSON = ROOT + "/reports/voltarget_10w_price50_scan.json"
OUT = ROOT + "/projects/Project_ATR_lowvol/results/voltarget扫描报告_20260815.md"


def load_perf(path):
    with open(path + "/summary.json", "r", encoding="utf-8") as f:
        return json.load(f)["performance"]


def main():
    with open(SCAN_JSON, "r", encoding="utf-8") as f:
        rows = json.load(f)  # [{vt, dir, perf}]

    print("=== 业绩指标（年化=CAGR，括号为线性年化）===")
    for r in rows:
        p = r["perf"]
        ann = p.get("cagr", p["annual_return"])
        print("VT=%s  总收益 %6.1f%%  年化 %5.2f%%(线性%5.2f%%)  回撤 %-6.2f%%  夏普 %.3f  卡玛 %.2f  胜率 %5.1f%%  交易 %d  未成交 %d"
              % (r["vt"], p["total_return"] * 100, ann * 100, p["annual_return"] * 100,
                 p["max_drawdown"] * 100, p["sharpe"], p.get("cagr_calmar", p["calmar"]),
                 p["win_rate"] * 100, p["n_trades"], p.get("unfilled_order_count", 0)))

    # 分年度 + 持仓 + 资金利用率
    yearly_all = {}
    extra = {}
    for r in rows:
        eq = pd.read_csv(r["dir"] + "/equity_curve.csv")
        pos = pd.read_csv(r["dir"] + "/positions.csv")
        eq["date"] = pd.to_datetime(eq["date"])
        eq["year"] = eq["date"].dt.year
        pos["month"] = pd.to_datetime(pos["date"]).dt.to_period("M")
        n_pos = pos.groupby("month")["code"].nunique()
        hold = eq[eq["market_value"] > 100]
        util = (1 - hold["cash"] / hold["total_asset"]).mean()
        yearly = {y: g["total_asset"].iloc[-1] / g["total_asset"].iloc[0] - 1
                  for y, g in eq.groupby("year")}
        yearly_all[r["vt"]] = yearly
        extra[r["vt"]] = (n_pos.mean(), n_pos.max(), n_pos.min(), util * 100)
        print("VT=%s  平均持仓 %.1f 只(max %d, min %d)  持仓期平均仓位 %.1f%%"
              % (r["vt"], n_pos.mean(), n_pos.max(), n_pos.min(), util * 100))

    vts = [r["vt"] for r in rows]
    all_years = sorted(set().union(*[set(yearly_all[v].keys()) for v in vts]))
    print("\n年份   | " + " | ".join("%-10s" % ("VT%g" % v if v else "基线0") for v in vts))
    for y in all_years:
        print("  %d  | " % y + " | ".join(
            "%+7.1f%%    " % (yearly_all[v].get(y, float("nan")) * 100) for v in vts))

    # 写 markdown 报告
    lines = []
    A = lines.append
    A("# ATR 10万/8只+价<50 vol_target 无杠杆扫描报告（2026-08-15）")
    A("")
    A("> **任务**: T-20260815-001 —— 在部署配置 `atr_10w_price50.yaml`（10万/8只/等权/无杠杆/真实价<50/2019-2026）")
    A("> 基础上叠加 vol_target 无杠杆 4 版扫描，看对 -24.8% 回撤的压降与收益代价。")
    A("> **口径**: 无杠杆时 target_leverage=1.0 为硬上限，vol_target 只能向下缩敞口（削回撤），不能加杠杆。")
    A("> **年化口径**: 下表年化为 **CAGR（复利）**，括号内为线性年化（旧口径）；卡玛=CAGR/回撤。")
    A("")
    A("## 一、业绩指标")
    A("")
    A("| 版本 | 总收益 | 年化(CAGR) | 最大回撤 | 夏普 | 卡玛 | 胜率 | 交易数 | 平均持仓 | 资金利用率 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        p = r["perf"]
        np_, nmax, nmin, util = extra[r["vt"]]
        ann = p.get("cagr", p["annual_return"])
        cmar = p.get("cagr_calmar", p["calmar"])
        A("| VT%s | %6.1f%% | %5.2f%%(线性%5.2f%%) | %-6.2f%% | %.3f | %.2f | %5.1f%% | %d | %.1f只 | %.1f%% |"
          % ("%g" % r["vt"] if r["vt"] else "0(基线)", p["total_return"] * 100,
             ann * 100, p["annual_return"] * 100, p["max_drawdown"] * 100, p["sharpe"], cmar,
             p["win_rate"] * 100, p["n_trades"], np_, util))
    A("")
    A("## 二、分年度")
    A("")
    A("| 年份 | " + " | ".join("VT%s" % ("%g" % v if v else "0") for v in vts) + " |")
    A("|---|---" + "|---" * len(vts) + "|")
    for y in all_years:
        A("| %d | " % y + " | ".join(
            "%+6.1f%%" % (yearly_all[v].get(y, float("nan")) * 100) for v in vts) + " |")
    A("")
    A("## 三、结论")
    A("")
    A("（由人工/诚哥依据上表补结论；原始数据在 reports/voltarget_10w_price50_scan.json 与各 report 目录）")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\nreport -> %s" % OUT)


if __name__ == "__main__":
    main()
