# coding=utf-8
"""VWAP量价相关因子 (gtja191_090)。

公式: -1 * rank(corr(rank(VWAP), rank(volume), 5))
  VWAP = amount / (volume × 100)  # 元/股
  5日滚动Spearman相关（截面rank后的时序相关），取负再截面rank。

IC参考 (小盘 1500-3000, 2021-2023):
  ICIR=0.505, IC均值=0.033, IC>0稳定性=70.1%
"""

import numpy as np
import pandas as pd

from factors.base import FactorBase


class VWAPVolumeCorr(FactorBase):
    """VWAP量价相关因子"""

    def __init__(self):
        super().__init__(
            name="vwap_volume_corr",
            category="量价",
            description="5日VWAP排名与成交量排名的Spearman秩相关，取负再排名"
        )

    def compute(self, panel, fin_ffill, **kwargs):
        """计算全时段VWAP量价相关因子值。

        Args:
            panel: MultiIndex(trade_date, ts_code) -> [vol, amount, ...]

        Returns:
            pd.Series: index=(trade_date, ts_code), values=因子值
        """
        # 取 volume 和 amount 列
        if "vol" not in panel.columns or "amount" not in panel.columns:
            raise ValueError("panel 缺少 vol 或 amount 列")

        # VWAP = amount / (volume × 100)
        vwap = panel["amount"] / (panel["vol"] * 100.0 + 1e-8)

        # 展开为宽表 (date × code)
        vwap_wide = vwap.unstack("ts_code")
        vol_wide = panel["vol"].unstack("ts_code")

        # 逐日截面 rank (axis=1: 每行内跨股票排名)
        vwap_rank = vwap_wide.rank(axis=1)
        vol_rank = vol_wide.rank(axis=1)

        # 5日滚动相关: rolling 沿时间轴 (axis=0)
        # rolling(5).corr(other) 计算两DataFrame间对应列的滚动相关
        # 由于输入是 ranks，结果 = Spearman 相关
        corr_wide = vwap_rank.rolling(5, min_periods=5).corr(vol_rank)

        # 处理前4天为NaN
        corr_wide = corr_wide.fillna(0.0)

        # 取负再截面rank (axis=1: 每行内跨股票排名)
        neg_corr = -corr_wide
        factor_wide = neg_corr.rank(axis=1)

        # 压缩回 MultiIndex
        result = factor_wide.stack()
        result.name = self.name
        return result


# ============================================================
# 单日计算版本（兼容旧接口，用于逐日调仓）
# ============================================================
def compute_single(panel, date, trade_dates, date_series):
    """计算单日 VWAP 因子截面值。

    Args:
        panel: MultiIndex DataFrame
        date: 目标日期
        trade_dates: 交易日列表
        date_series: 目标日截面 Series (用于对齐索引)

    Returns:
        pd.Series: 因子值, index=ts_code
    """
    date_idx = trade_dates.index(date)
    if date_idx < 5:
        return pd.Series(0.0, index=date_series.index)

    start = trade_dates[date_idx - 4]
    amount_wide = panel.loc[start:date, "amount"].unstack("ts_code")
    vol_wide = panel.loc[start:date, "vol"].unstack("ts_code")
    # 边界处理: volume=0(停牌)排除
    safe_mask = (vol_wide > 0).all(axis=0) & (amount_wide > 0).all(axis=0)
    vol_wide = vol_wide.loc[:, safe_mask]
    amount_wide = amount_wide.loc[:, safe_mask]

    if vol_wide.shape[1] == 0:
        return pd.Series(0.0, index=date_series.index)

    vwap_wide = amount_wide / (vol_wide * 100.0)
    # 逐日截面 rank (axis=0: 每列内跨股票排名)
    vwap_rank = vwap_wide.rank(axis=0)
    vol_rank = vol_wide.rank(axis=0)
    # 5日滚动相关
    corr_wide = vwap_rank.rolling(5, min_periods=5).corr(vol_rank)
    if len(corr_wide) > 0:
        latest_corr = corr_wide.iloc[-1]
        result = (-latest_corr.rank()).reindex(date_series.index)
    else:
        result = pd.Series(0.0, index=date_series.index)

    if result.isna().any():
        result = result.fillna(0.0)
    return result
