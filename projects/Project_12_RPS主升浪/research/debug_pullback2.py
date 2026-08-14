# coding: utf-8
"""调试测试版回调构造（修正格式）。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd
import numpy as np
from strategy.rps_momentum import _check_pullback_entry


def make_df(n=300, trend=0.004, vol_base=1e7):
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(42)
    rets = rng.normal(trend, 0.02, n)
    close = 10 * np.cumprod(1 + rets)
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.01, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.01, n)))
    vol = np.abs(rng.normal(vol_base, vol_base * 0.2, n))
    vol[-1] = vol_base * 2.5
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": open_, "high": high, "low": low, "close": close,
        "vol": vol, "amount": vol * close, "is_st": 0.0,
    })
    return df


df = make_df(trend=0.004)
close = df["close"].astype(float).values
recent_high_idx = len(close) - 6
peak = close[recent_high_idx]
df.loc[df.index[-1], "close"] = peak * 0.92
df.loc[df.index[-1], "high"] = peak
df.loc[df.index[-2], "high"] = peak

h = max(float(v) for v in df["high"].iloc[-21:-1])
last = float(df["close"].iloc[-1])
print("peak(close[-6])=%.4f" % peak)
print("recent_high=%.4f last=%.4f pullback=%.4f" % (h, last, (h - last) / h))
ma = float(df["close"].iloc[-20:].mean())
above = last >= ma * 0.93
print("ma20=%.4f above_ma=%s" % (ma, above))
print("result=", _check_pullback_entry(df, 20, 0.15, 0.02))

# 反向：无回调应拒绝
df2 = make_df(trend=0.004)
close2 = df2["close"].astype(float).values
df2.loc[df2.index[-1], "close"] = close2[-1] * 1.05
df2.loc[df2.index[-1], "high"] = close2[-1] * 1.05
print("no_pullback_result=", _check_pullback_entry(df2, 20, 0.15, 0.02))
