# coding: utf-8
"""分析大盘门控在 2020-2021 的行为：为什么牛市也空仓。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd

# 用 baseline 的 equity_curve 看 benchmark 与持仓关系
REPORT = "D:/QuantLab/reports/20260814_130548_428be0_diag_baseline"
eq = pd.read_csv(REPORT + "/equity_curve.csv")
eq["date"] = pd.to_datetime(eq["date"])
eq["year"] = eq["date"].dt.year
eq["holding"] = eq["market_value"] > 100

print("=== 2020 年：benchmark 与持仓 ===")
eq2020 = eq[eq["year"] == 2020]
print("2020 总交易日: %d, 持仓天数: %d" % (len(eq2020), eq2020["holding"].sum()))

# 看 benchmark 在 2020 的表现（用首尾）
bm_start = eq2020.iloc[0]["benchmark_close"]
bm_end = eq2020.iloc[-1]["benchmark_close"]
print("2020 benchmark: %.1f -> %.1f (%+.1f%%)" % (bm_start, bm_end, (bm_end/bm_start-1)*100))

# 看 MA60 与 benchmark 的关系（用策略内部判断近似：持仓状态翻转点）
print("\n=== benchmark 月均值走势（2020）===")
eq2020["month"] = eq2020["date"].dt.to_period("M")
monthly = eq2020.groupby("month").agg(
    bm_mean=("benchmark_close", "mean"),
    hold_days=("holding", "sum"),
).reset_index()
print(monthly.to_string(index=False))

# 检查 trade 日期分布
trades = pd.read_csv("D:/QuantLab/reports/20260814_130548_428be0_diag_baseline/trades.csv")
trades["date"] = pd.to_datetime(trades["date"])
trades["year"] = trades["date"].dt.year
print("\n=== 2020-2021 交易分布 ===")
print(trades.groupby(trades["date"].dt.to_period("M")).size().to_string())
