# coding=utf-8
"""双均线趋势策略：SMA 10/30 金叉死叉 + 5%止损"""

import numpy as np
import pandas as pd


# 策略参数
SHORT_MA = 10  # 短期均线
LONG_MA = 30   # 长期均线
STOP_LOSS = -0.05  # 止损线 -5%
MOMENTUM_WINDOW = 20  # 动量计算窗口
MOMENTUM_MAX = 0.3  # 动量上限（过滤暴涨股）


# 缓存：预计算所有股票的均线
_ma_cache = {}


def precompute_ma(panel):
    """预计算所有股票的均线，避免逐日重复计算。"""
    global _ma_cache
    if _ma_cache:
        return
    
    print("[dual_ma] 预计算均线...")
    
    # 提取收盘价面板 (date x code)
    close_panel = panel["close"].unstack("ts_code")
    
    # 计算均线
    sma_short = close_panel.rolling(SHORT_MA, min_periods=SHORT_MA).mean()
    sma_long = close_panel.rolling(LONG_MA, min_periods=LONG_MA).mean()
    
    # 计算金叉死叉
    golden = (sma_short > sma_long) & (sma_short.shift(1) <= sma_long.shift(1))
    death = (sma_short < sma_long) & (sma_short.shift(1) >= sma_long.shift(1))
    trend = (sma_short > sma_long).astype(int)
    
    # 计算动量
    momentum = close_panel / close_panel.shift(MOMENTUM_WINDOW) - 1.0
    
    _ma_cache = {
        "golden": golden,
        "death": death,
        "trend": trend,
        "momentum": momentum,
        "close": close_panel,
    }
    print("[dual_ma] 均线预计算完成")


def get_candidates(panel, date, max_positions=50):
    """获取买入候选股。"""
    if not _ma_cache:
        precompute_ma(panel)
    
    if date not in _ma_cache["golden"].index:
        return []
    
    golden = _ma_cache["golden"].loc[date]
    trend = _ma_cache["trend"].loc[date]
    momentum = _ma_cache["momentum"].loc[date]
    
    # 筛选：金叉或多头排列 + 动量在合理范围
    mask = (golden | (trend == 1)) & (momentum > 0) & (momentum < MOMENTUM_MAX)
    candidates = momentum[mask].sort_values(ascending=False)
    
    return candidates.index[:max_positions].tolist()


def should_sell(panel, date, code, entry_price):
    """判断是否需要卖出。"""
    if not _ma_cache:
        precompute_ma(panel)
    
    if date not in _ma_cache["death"].index:
        return False, ""
    
    # 死叉
    death = _ma_cache["death"].loc[date].get(code, False)
    if death:
        return True, "死叉"
    
    # 止损
    close = _ma_cache["close"].loc[date].get(code)
    if close is not None and not pd.isna(close):
        ret = close / entry_price - 1.0
        if ret <= STOP_LOSS:
            return True, "止损"
    
    return False, ""
