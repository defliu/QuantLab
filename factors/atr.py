# coding: utf-8
"""ATR (Average True Range) factors for strategy use.

ATR% = ATR / close  -- the volatility proxy the ATR low-vol strategy relies on.
Pure pandas; no side effects.
"""
import numpy as np


def true_range(high, low, close):
    """Vectorized true range series."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    return tr


def atr(df, n=14):
    """Latest ATR (absolute) over rolling window `n` (Wilder-style smoothing)."""
    if df is None or len(df) < n + 1:
        return 0.0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    tr = true_range(high, low, close)
    # Wilder's smoothing ~ EMA with alpha=1/n
    atr_series = tr.ewm(alpha=1.0 / n, adjust=False).mean()
    val = float(atr_series.iloc[-1])
    return val if val == val else 0.0  # guard NaN


def atr_pct(df, n=14):
    """Latest ATR as a percent of close price (ATR%)."""
    if df is None or len(df) < n + 1:
        return 0.0
    a = atr(df, n)
    last_close = float(df["close"].astype(float).iloc[-1])
    if last_close <= 0:
        return 0.0
    return a / last_close
