# coding=utf-8
"""深入调试：升势/高位/下跌三组的 RPS 与加速度时间序列。"""
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
                rng.normal(0.0002, 0.008, n_dates - 50),
                np.linspace(0.002, 0.015, 50),
            ])
        elif j < 20:
            rets = np.concatenate([
                rng.normal(0.004, 0.01, n_dates - 100),
                rng.normal(0.0002, 0.008, 100),
            ])
        else:
            rets = rng.normal(-0.002, 0.008, n_dates)
        close = 10 * np.cumprod(1 + rets)
        frames.append(pd.DataFrame({"close": close, "ts_code": code, "date": dates}))
    panel = pd.concat(frames).set_index(["date", "ts_code"])
    return panel


panel = make_panel()
close_wide = panel["close"].unstack("ts_code")
ret_wide = close_wide / close_wide.shift(20) - 1.0
rps_wide = ret_wide.rank(axis=1, pct=True) * 100.0
accel_wide = rps_wide - rps_wide.shift(5)

print("=== 末 25 天各组 RPS 均值（观察爬升 vs 封顶 vs 低位）===")
for group_name, lo, hi in [("升势组", 0, 10), ("高位组", 10, 20), ("下跌组", 20, 30)]:
    codes = ["%06d.SZ" % i for i in range(lo, hi)]
    series = rps_wide.loc[:, codes].mean(axis=1)
    vals = [round(x, 1) for x in series.iloc[-25:].values]
    print("%s: %s" % (group_name, vals))

print("\n=== 末 10 天各组 5日加速度均值 ===")
for group_name, lo, hi in [("升势组", 0, 10), ("高位组", 10, 20), ("下跌组", 20, 30)]:
    codes = ["%06d.SZ" % i for i in range(lo, hi)]
    series = accel_wide.loc[:, codes].mean(axis=1)
    vals = [round(x, 2) for x in series.iloc[-10:].values]
    print("%s: %s" % (group_name, vals))
