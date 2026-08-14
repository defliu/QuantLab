# coding: utf-8
"""全量回测分年度表现 + 基准对比。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd
import numpy as np

REPORT = "D:/QuantLab/reports/20260814_125257_7470e9_rps_momentum_v1_baseline"
eq = pd.read_csv(REPORT + "/equity_curve.csv")

# 分年度收益
eq["date"] = pd.to_datetime(eq["date"])
eq["year"] = eq["date"].dt.year

print("=== 分年度收益（策略 vs 沪深300）===")
rows = []
for year, grp in eq.groupby("year"):
    strat_start = grp.iloc[0]["total_asset"]
    strat_end = grp.iloc[-1]["total_asset"]
    bm_start = grp.iloc[0]["benchmark_close"]
    bm_end = grp.iloc[-1]["benchmark_close"]
    strat_ret = strat_end / strat_start - 1
    bm_ret = bm_end / bm_start - 1
    rows.append({
        "year": year,
        "strategy": strat_ret,
        "benchmark": bm_ret,
        "excess": strat_ret - bm_ret,
    })

df = pd.DataFrame(rows)
df["strategy"] = (df["strategy"] * 100).round(2)
df["benchmark"] = (df["benchmark"] * 100).round(2)
df["excess"] = (df["excess"] * 100).round(2)
print(df.to_string(index=False))

# 期末持仓市值分布（按年看持仓天数）
print("\n=== 每年持仓天数（market_value > 0 的天数）===")
eq["holding"] = eq["market_value"] > 100
hold_days = eq.groupby("year")["holding"].sum()
print(hold_days.to_string())
