# coding: utf-8
"""对比单独跑 vs 全周期：2020 年逐月净值 + 持仓数，定位矛盾本质。"""
import pandas as pd

SEP = "D:/QuantLab/reports/20260814_133943_d2a5d2_v3_balanced_2020"
FULL = "D:/QuantLab/reports/20260814_134314_2d6b0d_rps_momentum_v3_balanced"

sep_eq = pd.read_csv(SEP + "/equity_curve.csv")
full_eq = pd.read_csv(FULL + "/equity_curve.csv")
sep_pos = pd.read_csv(SEP + "/positions.csv")
full_pos = pd.read_csv(FULL + "/positions.csv")

sep_eq["date"] = pd.to_datetime(sep_eq["date"])
full_eq["date"] = pd.to_datetime(full_eq["date"])

# 每月末资产 + 持仓数
sep_eq["month"] = sep_eq["date"].dt.to_period("M")
full_eq["month"] = full_eq["date"].dt.to_period("M")
sep_pos["month"] = pd.to_datetime(sep_pos["date"]).dt.to_period("M")
full_pos["month"] = pd.to_datetime(full_pos["date"]).dt.to_period("M")

sep_monthly = sep_eq.groupby("month").last()[["total_asset"]].reset_index()
full_monthly = full_eq.groupby("month").last()[["total_asset"]].reset_index()
sep_pos_cnt = sep_pos.groupby("month")["code"].nunique().reset_index()
full_pos_cnt = full_pos.groupby("month")["code"].nunique().reset_index()

print("=== 2020 年每月末：单独跑 vs 全周期 ===")
print("month | 单独资产 | 全周期资产 | 单独持仓数 | 全周期持仓数")
for m in sep_monthly["month"]:
    if str(m).startswith("2020"):
        sa = sep_monthly[sep_monthly["month"] == m]["total_asset"].values[0]
        fa_vals = full_monthly[full_monthly["month"] == m]["total_asset"].values
        fa = fa_vals[0] if len(fa_vals) else float("nan")
        sp = sep_pos_cnt[sep_pos_cnt["month"] == m]["code"].values
        fp = full_pos_cnt[full_pos_cnt["month"] == m]["code"].values
        sp = sp[0] if len(sp) else 0
        fp = fp[0] if len(fp) else 0
        print("%s | %.0f | %.0f | %d | %d" % (m, sa, fa, sp, fp))
