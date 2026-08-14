# coding: utf-8
"""分析冒烟 v3：净值曲线 + 基准对比 + 配对盈亏。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd
import numpy as np

REPORT = "D:/QuantLab/reports/20260814_124805_6b6bae_rps_momentum_v1_smoke"

eq = pd.read_csv(REPORT + "/equity_curve.csv")
print("=== equity_curve 列 ===")
print(list(eq.columns))
print("\n=== 前 3 行 ===")
print(eq.head(3).to_string())
print("\n=== 后 3 行 ===")
print(eq.tail(3).to_string())

# 基准对比：沪深300 2023-2024
print("\n=== 沪深300 2023-2024 参考 ===")
print("2023 沪深300: 约 -11.4%")
print("2024 沪深300: 约 -14.7%")
print("两年累计: 约 -24%")

# 配对盈亏
trades = pd.read_csv(REPORT + "/trades.csv")
from collections import defaultdict, deque
positions = defaultdict(deque)
trades_out = []
for _, row in trades.iterrows():
    code, vol, price, side, date = row["code"], int(row["volume"]), float(row["price"]), row["side"], row["date"]
    if side == "buy":
        positions[code].append([vol, price, date])
    else:
        remaining = vol
        while remaining > 0 and positions[code]:
            b = positions[code][0]
            b_vol, b_price, b_date = b
            matched = min(b_vol, remaining)
            pnl = (price - b_price) * matched
            trades_out.append({
                "code": code, "buy_date": b_date, "sell_date": date,
                "vol": matched, "ret": (price - b_price) / b_price, "pnl": pnl,
            })
            remaining -= matched
            if matched == b_vol:
                positions[code].popleft()
            else:
                b[0] -= matched
                remaining = 0

td = pd.DataFrame(trades_out)
print("\n=== 配对盈亏分析 ===")
print("完整交易数: %d" % len(td))
if len(td) > 0:
    wins = td[td["pnl"] > 0]
    losses = td[td["pnl"] < 0]
    print("胜率: %.1f%% (%d/%d)" % (len(wins)/len(td)*100, len(wins), len(td)))
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0
    print("平均盈利: %.0f 元, 平均亏损: %.0f 元, 盈亏比: %.2f" % (avg_win, avg_loss, avg_win/avg_loss if avg_loss else 0))
    td["buy_date"] = pd.to_datetime(td["buy_date"])
    td["sell_date"] = pd.to_datetime(td["sell_date"])
    td["hold_days"] = (td["sell_date"] - td["buy_date"]).dt.days
    print("平均持有期: %.1f 天" % td["hold_days"].mean())
    print("平均单笔收益: %.2f%%" % (td["ret"].mean()*100))
    print("总已实现盈亏: %.0f 元" % td["pnl"].sum())
    print("\n=== 收益分布 ===")
    print(td["ret"].describe().to_string())
