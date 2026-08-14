# coding: utf-8
"""调试回调买入判断。"""
import sys
sys.path.insert(0, "D:/QuantLab")
import pandas as pd
import numpy as np
from strategy.rps_momentum import _check_pullback_entry

rng = np.random.default_rng(42)
n = 300
rets = rng.normal(0.004, 0.02, n)
close = 10 * np.cumprod(1 + rets)
dates = pd.bdate_range("2023-01-01", periods=n)
df = pd.DataFrame({
    "date": dates.strftime("%Y-%m-%d"),
    "open": close * 0.99,
    "high": close * 1.02,
    "low": close * 0.98,
    "close": close,
    "vol": np.abs(rng.normal(1e7, 2e6, n)),
    "is_st": 0.0,
})

# 制造回调：最后一天从 6 天前的高点回落 8%
peak = float(df["high"].iloc[-6])
df.loc[df.index[-1], "close"] = peak * 0.92
df.loc[df.index[-1], "high"] = peak
df.loc[df.index[-2], "high"] = peak

h = max(float(v) for v in df["high"].iloc[-21:-1])
last = float(df["close"].iloc[-1])
print("recent_high(no today)=%.4f last=%.4f pullback=%.4f" % (h, last, (h - last) / h))
ma = float(df["close"].iloc[-20:].mean())
print("ma20=%.4f above_ma=%s" % (ma, last >= ma * 0.98))
print("result=", _check_pullback_entry(df, 20, 0.15, 0.02))
