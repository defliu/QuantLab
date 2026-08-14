# coding: utf-8
"""对比 10万版 vs 100万版：分年度 + 持仓数。"""
import pandas as pd

REPORTS = {
    "10万/8只": "D:/QuantLab/reports/20260814_162537_cadab0_atr_lowvol_10w_8",
    "100万/100只": "D:/QuantLab/reports/20260814_163946_9c2b82_atr_lowvol_100w_100",
}

yearly = {}
for name, path in REPORTS.items():
    eq = pd.read_csv(path + "/equity_curve.csv")
    pos = pd.read_csv(path + "/positions.csv")
    eq["date"] = pd.to_datetime(eq["date"])
    eq["year"] = eq["date"].dt.year
    pos["month"] = pd.to_datetime(pos["date"]).dt.to_period("M")
    n_pos = pos.groupby("month")["code"].nunique()

    yearly[name] = {}
    for y, g in eq.groupby("year"):
        yearly[name][y] = g["total_asset"].iloc[-1] / g["total_asset"].iloc[0] - 1

    print("%s: 平均持仓 %.1f 只 (max %d, min %d)" % (name, n_pos.mean(), n_pos.max(), n_pos.min()))

print("\n=== 分年度对比 ===")
print("年份    | %-10s | %-12s" % (list(yearly.keys())[0], list(yearly.keys())[1]))
all_years = sorted(set(yearly[list(yearly.keys())[0]].keys()) | set(yearly[list(yearly.keys())[1]].keys()))
for y in all_years:
    v1 = yearly[list(yearly.keys())[0]].get(y, float('nan'))
    v2 = yearly[list(yearly.keys())[1]].get(y, float('nan'))
    print("  %d    | %+6.1f%%        | %+6.1f%%" % (y, v1 * 100, v2 * 100))
