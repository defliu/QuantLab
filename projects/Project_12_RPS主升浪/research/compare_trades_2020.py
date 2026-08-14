# coding: utf-8
"""对比单独跑 vs 全周期在 2020 年的交易行为差异。"""
import pandas as pd

SEP = "D:/QuantLab/reports/20260814_133943_d2a5d2_v3_balanced_2020/trades.csv"
FULL = "D:/QuantLab/reports/20260814_134314_2d6b0d_rps_momentum_v3_balanced/trades.csv"

sep = pd.read_csv(SEP)
full = pd.read_csv(FULL)

sep["year"] = sep["date"].str[:4]
full["year"] = full["date"].str[:4]

print("=== 单独跑 2020 年交易 ===")
sep_2020 = sep[sep["year"] == "2020"]
print("交易数: %d" % len(sep_2020))
print(sep_2020[["date", "code", "side", "volume", "price", "reason"]].head(30).to_string())

print("\n=== 全周期 2020 年交易 ===")
full_2020 = full[full["year"] == "2020"]
print("交易数: %d" % len(full_2020))
if len(full_2020) > 0:
    print(full_2020[["date", "code", "side", "volume", "price", "reason"]].head(30).to_string())
else:
    print("（无交易）")
