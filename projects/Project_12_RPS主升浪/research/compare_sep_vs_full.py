# coding: utf-8
"""对比 V4 单独跑 vs 全周期在 2020 年的表现差异，排查矛盾根源。"""
import pandas as pd

SEP = "D:/QuantLab/reports/20260814_133943_d2a5d2_v3_balanced_2020"   # 单独 2020-2021
FULL = "D:/QuantLab/reports/20260814_134314_2d6b0d_rps_momentum_v3_balanced"  # 全周期

sep_eq = pd.read_csv(SEP + "/equity_curve.csv")
full_eq = pd.read_csv(FULL + "/equity_curve.csv")

sep_eq["date"] = pd.to_datetime(sep_eq["date"])
full_eq["date"] = pd.to_datetime(full_eq["date"])

# 对比 2020 年每月末净值
sep_2020 = sep_eq[sep_eq["date"].dt.year == 2020].copy()
full_2020 = full_eq[full_eq["date"].dt.year == 2020].copy()

sep_2020["month"] = sep_2020["date"].dt.to_period("M")
full_2020["month"] = full_2020["date"].dt.to_period("M")

sep_m = sep_2020.groupby("month").last()[["total_asset", "market_value"]].reset_index()
full_m = full_2020.groupby("month").last()[["total_asset", "market_value"]].reset_index()

print("=== 2020 每月末净值对比 ===")
print("month    | 单独跑total_asset | 全周期total_asset | 单独市值 | 全周期市值")
for i in range(len(sep_m)):
    m = sep_m.iloc[i]["month"]
    sa = sep_m.iloc[i]["total_asset"]
    fa = full_m[full_m["month"] == m]["total_asset"].values
    sm = sep_m.iloc[i]["market_value"]
    fm = full_m[full_m["month"] == m]["market_value"].values
    fa = fa[0] if len(fa) else float('nan')
    fm = fm[0] if len(fm) else float('nan')
    print("%s | %.0f | %.0f | %.0f | %.0f" % (m, sa, fa, sm, fm))

# 起始资金
print("\n单独跑起始: %.0f, 全周期 2020-01 起始: %.0f" % (
    sep_eq.iloc[0]["total_asset"],
    full_2020.iloc[0]["total_asset"]))
