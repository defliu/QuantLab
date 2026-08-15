# coding: utf-8
"""分析 10万版 ATR 分年度 + 持仓数 + 拒单情况。"""
import pandas as pd

REPORT = "D:/QuantLab/reports/20260814_162537_cadab0_atr_lowvol_10w_8"
eq = pd.read_csv(REPORT + "/equity_curve.csv")
pos = pd.read_csv(REPORT + "/positions.csv")

eq["date"] = pd.to_datetime(eq["date"])
eq["year"] = eq["date"].dt.year

print("=== 10万版 ATR 分年度（累计收益）===")
for y, g in eq.groupby("year"):
    r = g["total_asset"].iloc[-1] / g["total_asset"].iloc[0] - 1
    print("  %d: %+.1f%%" % (y, r * 100))

# 持仓数分布
pos["date"] = pd.to_datetime(pos["date"])
pos["year"] = pos["date"].dt.year
pos["month"] = pos["date"].dt.to_period("M")
n_pos = pos.groupby("month")["code"].nunique()
print("\n=== 每期实际持仓数（应该≤8）===")
print("  均值: %.1f 只, 最大: %d, 最小: %d" % (n_pos.mean(), n_pos.max(), n_pos.min()))
print("  分布:")
print(n_pos.value_counts().sort_index().to_string())

# 期末资产
print("\n期末资产: %.0f (起始10万)" % eq["total_asset"].iloc[-1])
