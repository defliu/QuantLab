# coding=utf-8
"""调试修正后的加速度因子：涨幅加速度 vs 排名差。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import numpy as np
import pandas as pd


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

# 方案A：排名差（原实现）ΔRPS = RPS_t - RPS_{t-5}
ret_wide = close_wide / close_wide.shift(20) - 1.0
rps_wide = ret_wide.rank(axis=1, pct=True) * 100.0
accel_rank = rps_wide - rps_wide.shift(5)

# 方案B：涨幅加速度 = 近5日涨幅 - 前一个5日涨幅（自身加速）
ret5 = close_wide / close_wide.shift(5) - 1.0
accel_ret = ret5 - ret5.shift(5)
# 再截面排名
accel_ret_rank = accel_ret.rank(axis=1, pct=True) * 100.0

print("=== 末日期三组：方案A(排名差) vs 方案B(涨幅加速度) ===")
for group_name, lo, hi in [("升势组", 0, 10), ("高位组", 10, 20), ("下跌组", 20, 30)]:
    codes = ["%06d.SZ" % i for i in range(lo, hi)]
    last = close_wide.index[-1]
    a = accel_rank.loc[last, codes].mean()
    b = accel_ret_rank.loc[last, codes].mean()
    raw_b = accel_ret.loc[last, codes].mean()
    print("  %s: 方案A排名差=%.2f | 方案B涨幅加速度rank=%.2f (raw=%.5f)" % (group_name, a, b, raw_b))

# 看方案B的原始加速度（raw，未排名）
print("\n=== 末 15 天 方案B raw 涨幅加速度（应升势组最高）===")
for group_name, lo, hi in [("升势组", 0, 10), ("高位组", 10, 20), ("下跌组", 20, 30)]:
    codes = ["%06d.SZ" % i for i in range(lo, hi)]
    series = accel_ret.loc[:, codes].mean(axis=1)
    vals = [round(x, 4) for x in series.iloc[-15:].values]
    print("%s: %s" % (group_name, vals))
