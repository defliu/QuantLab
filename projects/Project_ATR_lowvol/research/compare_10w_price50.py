# coding: utf-8
"""三版对比：10万/8只 vs 10万/8只+价50过滤 vs 100万/100只。
指标：总收益/年化/回撤/夏普（summary.json）+ 分年度 + 平均持仓数 + 资金利用率。
"""
import json
import pandas as pd

REPORTS = {
    "10万/8只(基线)":    "D:/QuantLab/reports/20260814_162537_cadab0_atr_lowvol_10w_8",
    "10万/8只+价<50":    "D:/QuantLab/reports/20260814_173534_8fb797_atr_lowvol_10w_price50",
    "100万/100只(对照)": "D:/QuantLab/reports/20260814_163946_9c2b82_atr_lowvol_100w_100",
}

def load_perf(path):
    with open(path + "/summary.json", "r", encoding="utf-8") as f:
        return json.load(f)["performance"]

# 1) 业绩指标
print("=== 业绩指标（年化=CAGR）===")
perfs = {}
for name, path in REPORTS.items():
    p = load_perf(path)
    perfs[name] = p
    ann = p.get("cagr", p["annual_return"])
    print("%-14s 总收益 %6.1f%%  年化 %5.2f%%(线性%5.2f%%)  回撤 %-6.2f%%  夏普 %.3f  胜率 %5.1f%%  交易 %d"
          % (name, p["total_return"] * 100, ann * 100, p["annual_return"] * 100,
             p["max_drawdown"] * 100, p["sharpe"], p["win_rate"] * 100, p["n_trades"]))

# 2) 分年度 + 持仓数 + 资金利用率
print("\n=== 分年度（净值年收益）===")
yearly = {}
for name, path in REPORTS.items():
    eq = pd.read_csv(path + "/equity_curve.csv")
    pos = pd.read_csv(path + "/positions.csv")
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    pos["month"] = pd.to_datetime(pos["date"]).dt.to_period("M")
    n_pos = pos.groupby("month")["code"].nunique()
    hold = eq[eq["market_value"] > 100]
    util = (1 - hold["cash"] / hold["total_asset"]).mean()
    yearly[name] = {y: g["total_asset"].iloc[-1] / g["total_asset"].iloc[0] - 1
                    for y, g in eq.groupby("year")}
    print("%s: 平均持仓 %.1f 只(max %d,min %d), 持仓期平均仓位 %.1f%%"
          % (name, n_pos.mean(), n_pos.max(), n_pos.min(), util * 100))

names = list(REPORTS.keys())
all_years = sorted(set().union(*[set(yearly[n].keys()) for n in names]))
print("\n年份   | " + " | ".join("%-12s" % n for n in names))
for y in all_years:
    print("  %d  | " % y + " | ".join(
        "%+6.1f%%      " % (yearly[n].get(y, float('nan')) * 100) for n in names))
