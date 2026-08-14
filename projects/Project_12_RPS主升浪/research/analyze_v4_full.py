# coding: utf-8
"""V4 全周期分年度 + 配对盈亏 + 加仓分析。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd

REPORT = "D:/QuantLab/reports/20260814_134314_2d6b0d_rps_momentum_v3_balanced"
eq = pd.read_csv(REPORT + "/equity_curve.csv")
eq["date"] = pd.to_datetime(eq["date"])
eq["year"] = eq["date"].dt.year
eq["holding"] = eq["market_value"] > 100

print("=== V4 全周期分年度 ===")
for year, grp in eq.groupby("year"):
    strat = (grp.iloc[-1]["total_asset"] / grp.iloc[0]["total_asset"] - 1) * 100
    bm = (grp.iloc[-1]["benchmark_close"] / grp.iloc[0]["benchmark_close"] - 1) * 100
    print("  %d: 策略=%+.2f%% 沪深300=%+.2f%% 超额=%+.2f%% 持仓天数=%d/%d" % (
        year, strat, bm, strat - bm, grp["holding"].sum(), len(grp)))

trades = pd.read_csv(REPORT + "/trades.csv")
print("\n=== 交易原因分布 ===")
print(trades["reason"].value_counts().to_string())

# 加仓分析：rebalance_increase 按年
inc = trades[trades["reason"] == "rebalance_increase"]
if len(inc) > 0:
    inc["year"] = inc["date"].str[:4]
    print("\n=== rebalance_increase（加仓）按年 ===")
    print(inc.groupby("year").size().to_string())

# 配对盈亏
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
            trades_out.append({
                "code": code, "buy_date": b_date, "sell_date": date,
                "ret": (price - b_price) / b_price, "pnl": (price - b_price) * matched})
            remaining -= matched
            if matched == b_vol:
                positions[code].popleft()
            else:
                b[0] -= matched
                remaining = 0

td = pd.DataFrame(trades_out)
print("\n=== 配对盈亏 ===")
if len(td) > 0:
    wins = td[td["pnl"] > 0]
    losses = td[td["pnl"] < 0]
    print("完整交易: %d" % len(td))
    print("胜率: %.1f%%" % (len(wins) / len(td) * 100))
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = abs(losses["pnl"].mean()) if len(losses) else 0
    print("平均盈利: %.0f 平均亏损: %.0f 盈亏比: %.2f" % (avg_win, avg_loss, avg_win / avg_loss if avg_loss else 0))
    print("平均单笔: %+.2f%%" % (td["ret"].mean() * 100))
    print("总已实现盈亏: %.0f 元" % td["pnl"].sum())
    # 按年盈亏
    td["buy_year"] = td["buy_date"].str[:4]
    print("\n=== 按买入年份盈亏 ===")
    print(td.groupby("buy_year")["pnl"].sum().to_string())
