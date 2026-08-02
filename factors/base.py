# coding=utf-8
"""因子基类 + 预处理"""

import numpy as np
import pandas as pd
from abc import ABC, abstractmethod


class FactorBase(ABC):
    """因子基类"""

    def __init__(self, name, category, description=""):
        self.name = name
        self.category = category
        self.description = description

    @abstractmethod
    def compute(self, panel, fin_ffill, **kwargs):
        """计算因子值

        Args:
            panel: 面板数据 MultiIndex(date, code) -> [close, open, vol, ...]
            fin_ffill: 财务数据宽表

        Returns:
            pd.Series: 因子值，index=(date, code)
        """
        pass

    @staticmethod
    def winsorize(series, lower=0.01, upper=0.99):
        """百分位截尾"""
        if series.dropna().empty:
            return series
        lo = series.quantile(lower)
        hi = series.quantile(upper)
        return series.clip(lo, hi)

    @staticmethod
    def zscore(series):
        """Z-score标准化"""
        mu = series.mean()
        std = series.std()
        if std == 0 or np.isnan(std):
            return pd.Series(np.nan, index=series.index)
        return (series - mu) / std

    @staticmethod
    def rank_normalize(series):
        """排序标准化到 [0, 1]"""
        return series.rank(pct=True)
