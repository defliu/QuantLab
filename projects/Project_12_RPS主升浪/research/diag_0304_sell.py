# coding: utf-8
"""诊断：为什么全周期跑 2020-03-03 买入后 3-04 就卖出。

假设：_hold_decision 的移动止盈/止损在 3-04 触发了 target_exit。
检查 3-03 买入的 7 只票在 3-04 的移动止盈计算。
"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd
import numpy as np
from strategy.rps_momentum import _hold_decision, _peak_since_entry

# 从全周期 report 读 3-03/3-04 的持仓和卖出
FULL = "D:/QuantLab/reports/20260814_134314_2d6b0d_rps_momentum_v3_balanced"
trades = pd.read_csv(FULL + "/trades.csv")
positions_csv = pd.read_csv(FULL + "/positions.csv")

print("=== positions.csv 列 ===")
print(list(positions_csv.columns))

# 看 3-04 持仓状态
pos_0304 = positions_csv[positions_csv["date"] == "2020-03-04"]
print("\n=== 2020-03-04 持仓 ===")
if len(pos_0304) > 0:
    print(pos_0304.to_string())
else:
    print("（空）")

# 3-04 的卖出
sells_0304 = trades[(trades["date"] == "2020-03-04") & (trades["side"] == "sell")]
print("\n=== 2020-03-04 卖出 ===")
print(sells_0304.to_string())
