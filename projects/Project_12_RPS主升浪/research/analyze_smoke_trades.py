# coding: utf-8
"""分析 RPS 主升浪冒烟测试交易明细，诊断盈亏结构。"""
import pandas as pd

TRADES = "D:/QuantLab/reports/20260814_122727_6a00b4_rps_momentum_v1_smoke/trades.csv"
EQUITY = "D:/QuantLab/reports/20260814_122727_6a00b4_rps_momentum_v1_smoke/equity_curve.csv"

df = pd.read_csv(TRADES)
print("=== 交易概览 ===")
print("总交易数: %d" % len(df))
print("\n按 side 统计:")
print(df["side"].value_counts().to_string())
print("\n按 reason 统计:")
print(df["reason"].value_counts().to_string())
print("\n按年份统计交易数:")
df["year"] = df["date"].str[:4]
print(df.groupby("year")["side"].value_counts().unstack(fill_value=0).to_string())

# 检查原因字段
print("\n=== reason 字段样本 ===")
print(df["reason"].dropna().unique()[:20])
