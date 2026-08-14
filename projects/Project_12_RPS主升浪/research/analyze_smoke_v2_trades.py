# coding: utf-8
"""分析 RPS 冒烟 v2 交易明细：盈亏结构 + 持有期 + 退出原因。"""
import pandas as pd

TRADES = "D:/QuantLab/reports/20260814_123912_30c3b9_rps_momentum_v1_smoke/trades.csv"

df = pd.read_csv(TRADES)
print("=== 交易概览 ===")
print("总交易数: %d" % len(df))
print("\n按 side 统计:")
print(df["side"].value_counts().to_string())
print("\n按 reason 统计:")
print(df["reason"].value_counts().to_string())
print("\n按年份:")
df["year"] = df["date"].str[:4]
print(df.groupby("year")["side"].value_counts().unstack(fill_value=0).to_string())

# 卖出分析
sells = df[df["side"] == "sell"]
print("\n=== 卖出明细（前 20 笔）===")
cols = ["date", "code", "volume", "price", "amount", "reason"]
print(sells[cols].head(20).to_string())

# 买卖配对计算每笔盈亏
print("\n=== 按 code 汇总（买卖配对）===")
buys = df[df["side"] == "buy"].groupby("code").agg(
    total_buy_amt=("amount", "sum"), n_buy=("volume", "count"))
print(buys.head(10).to_string())
