# coding: utf-8
"""Volatility helpers shared by strategies and the rebalance overlay."""
import numpy as np


def ann_vol(df, n=60):
    """Trailing annualized volatility from close prices (last `n` bars).

    Returns 0.0 if insufficient data. Used for vol-parity sizing and the
    ex-ante vol-targeting estimate in backtest/rebalance.py.
    """
    if df is None or len(df) < 2:
        return 0.0
    close = df["close"]
    if not hasattr(close, "pct_change"):
        return 0.0
    rets = close.astype(float).pct_change().dropna()
    if len(rets) < 5:
        return 0.0
    sub = rets.iloc[-n:]
    if len(sub) < 2:
        return 0.0
    sd = float(sub.std())
    if sd <= 0 or sd != sd:
        return 0.0
    return sd * (252.0 ** 0.5)
