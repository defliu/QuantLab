# -*- coding: utf-8 -*-
"""绩效指标 —— 从 NAV 曲线计算年化/夏普/回撤等。

与 backtest/analyzer.py 的口径保持一致（CAGR 复利 + 252 交易日年化），
但因样本期通常只有几十个交易日，同时输出「年化（仅供参考）」和「区间累计收益」。
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252


def compute_metrics(nav_df, start_capital):
    """nav_df: 含 date / total_asset 的 DataFrame（升序）。返回 dict。"""
    if nav_df is None or len(nav_df) == 0:
        return {}
    df = nav_df.copy().reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    eq = df["total_asset"].astype(float)
    n = len(eq)
    ret = eq.pct_change().fillna(0.0)

    total_ret = eq.iloc[-1] / start_capital - 1.0
    days = (df["date"].iloc[-1] - df["date"].iloc[0]).days
    years = max(n / float(TRADING_DAYS), 1e-9)
    cagr = (eq.iloc[-1] / start_capital) ** (1.0 / years) - 1.0 if years > 0 and eq.iloc[-1] > 0 else np.nan

    vol = ret.std(ddof=1) * np.sqrt(TRADING_DAYS) if n > 1 else np.nan
    sharpe = (ret.mean() * TRADING_DAYS) / vol if vol and vol > 0 else np.nan

    cum = eq / start_capital
    peak = cum.cummax()
    dd = cum / peak - 1.0
    max_dd = float(dd.min()) if len(dd) else np.nan

    return {
        "start": df["date"].iloc[0].strftime("%Y-%m-%d"),
        "end": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "n_days": n,
        "calendar_days": days,
        "start_capital": round(float(start_capital), 2),
        "end_asset": round(float(eq.iloc[-1]), 2),
        "total_return": round(float(total_ret), 6),
        "cagr": round(float(cagr), 6) if np.isfinite(cagr) else None,
        "vol_annual": round(float(vol), 6) if np.isfinite(vol) else None,
        "sharpe": round(float(sharpe), 4) if np.isfinite(sharpe) else None,
        "max_drawdown": round(max_dd, 6) if np.isfinite(max_dd) else None,
    }
