# coding: utf-8
"""RPS 冒烟 v2：买卖配对计算每笔完整交易盈亏 + 盈亏比。

用 FIFO 方法把买入/卖出配对成完整交易，计算每笔的收益。
"""
import pandas as pd
from collections import defaultdict, deque

TRADES = "D:/QuantLab/reports/20260814_123912_30c3b9_rps_momentum_v1_smoke/trades.csv"

df = pd.read_csv(TRADES)
df = df.sort_values(["code", "date"]).reset_index(drop=True)

# FIFO 配对
positions = defaultdict(deque)  # code -> deque of (vol, cost_per_share, buy_date)
trades = []  # 完整交易记录

for _, row in df.iterrows():
    code = row["code"]
    vol = int(row["volume"])
    price = float(row["price"])
    side = row["side"]
    date = row["date"]

    if side == "buy":
        positions[code].append([vol, price, date])
    else:  # sell
        remaining = vol
        while remaining > 0 and positions[code]:
            b = positions[code][0]
            b_vol = b[0]
            if b_vol <= remaining:
                # 完全匹配这笔买入
                pnl = (price - b[1]) * b_vol
                trades.append({
                    "code": code,
                    "buy_date": b[2],
                    "sell_date": date,
                    "vol": b_vol,
                    "buy_price": b[1],
                    "sell_price": price,
                    "ret": (price - b[1]) / b[1],
                    "pnl": pnl,
                })
                remaining -= b_vol
                positions[code].popleft()
            else:
                # 部分匹配
                pnl = (price - b[1]) * remaining
                trades.append({
                    "code": code,
                    "buy_date": b[2],
                    "sell_date": date,
                    "vol": remaining,
                    "buy_price": b[1],
                    "sell_price": price,
                    "ret": (price - b[1]) / b[1],
                    "pnl": pnl,
                })
                b[0] -= remaining
                remaining = 0

trades_df = pd.DataFrame(trades)
print("=== 完整交易配对 ===")
print("完整交易数: %d" % len(trades_df))

if len(trades_df) > 0:
    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] < 0]
    print("\n胜率: %.1f%% (%d/%d)" % (len(wins) / len(trades_df) * 100,
                                       len(wins), len(trades_df)))

    avg_win = wins["pnl"].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) > 0 else 0
    print("平均盈利: %.2f 元" % avg_win)
    print("平均亏损: %.2f 元" % avg_loss)
    if avg_loss > 0:
        print("盈亏比: %.2f" % (avg_win / avg_loss))
    else:
        print("盈亏比: N/A")

    # 平均收益
    print("\n平均单笔收益: %.4f (%.2f%%)" % (trades_df["ret"].mean(), trades_df["ret"].mean() * 100))
    print("总已实现盈亏: %.2f 元" % trades_df["pnl"].sum())

    # 持有期分析
    trades_df["buy_date"] = pd.to_datetime(trades_df["buy_date"])
    trades_df["sell_date"] = pd.to_datetime(trades_df["sell_date"])
    trades_df["hold_days"] = (trades_df["sell_date"] - trades_df["buy_date"]).dt.days
    print("\n平均持有期: %.1f 天" % trades_df["hold_days"].mean())

    # 收益分布
    print("\n=== 收益分布 ===")
    print(trades_df["ret"].describe().to_string())

    # 大亏单
    print("\n=== 最大亏损单（前 10）===")
    worst = trades_df.nsmallest(10, "pnl")
    print(worst[["code", "buy_date", "sell_date", "buy_price", "sell_price", "ret", "pnl"]].to_string())
