# coding=utf-8
"""调试 RPS 加速度因子的方向问题。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import numpy as np
import pandas as pd
from factors.rps_acceleration import RPSAcceleration


def make_panel(n_dates=200, n_stocks=30, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n_dates)
    codes = ["%06d.SZ" % i for i in range(n_stocks)]
    frames = []
    for j, code in enumerate(codes):
        if j < 10:
            rets = np.concatenate([
                rng.normal(0.0005, 0.01, n_dates - 30),
                np.linspace(0.001, 0.01, 30),
            ])
        elif j < 20:
            rets = rng.normal(0.001, 0.01, n_dates)
        else:
            rets = rng.normal(-0.002, 0.01, n_dates)
        close = 10 * np.cumprod(1 + rets)
        frames.append(pd.DataFrame({"close": close, "ts_code": code, "date": dates}))
    panel = pd.concat(frames).set_index(["date", "ts_code"])
    return panel


panel = make_panel()
f = RPSAcceleration(window=20, accel_window=5)
result = f.compute(panel, None)

# 看最后几天的 RPS 和加速度
close_wide = panel["close"].unstack("ts_code")
ret_wide = close_wide / close_wide.shift(20) - 1.0
rps_wide = ret_wide.rank(axis=1, pct=True) * 100.0
accel_wide = rps_wide - rps_wide.shift(5)

print("=== 末日期 3 组股票的 RPS / 加速度 ===")
last_date = panel.index.get_level_values("date").max()
print("last_date:", last_date)
for group_name, lo, hi in [("加速组", 0, 10), ("平稳组", 10, 20), ("下跌组", 20, 30)]:
    codes = ["%06d.SZ" % i for i in range(lo, hi)]
    rps_vals = rps_wide.loc[last_date, codes].mean()
    acc_vals = accel_wide.loc[last_date, codes].mean()
    print("  %s: RPS=%.1f, 加速度=%.2f" % (group_name, rps_vals, acc_vals))

# 看时间序列：末 30 天的 RPS 变化
print("\n=== 末 15 天各组 RPS 均值变化 ===")
for group_name, lo, hi in [("加速组", 0, 10), ("下跌组", 20, 30)]:
    codes = ["%06d.SZ" % i for i in range(lo, hi)]
    series = rps_wide.loc[:, codes].mean(axis=1)
    print("%s 末15天RPS: %s" % (group_name, [round(x, 1) for x in series.iloc[-15:].values]))
