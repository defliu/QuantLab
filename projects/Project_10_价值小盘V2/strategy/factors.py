# coding=utf-8
"""Project_10 P0-2 因子层扩展: ATR% / 换手率 因子 (2026-08-04)

依据: specs/CodexQT-2026-0803_P0-2_因子层扩展.md
  - ATR% < 6%: 5年 spread +0.313%, 通过率 90.2% (有效因子库实测最强)
  - 换手率 1-8%: 5年 spread +0.158% (流动性适中, 倒U型映射)
  - 技术因子(均线/量价/动量/RSI/MACD/形态)近5年全部无效, 不碰

预处理顺序 (与 factors/base.py 约定一致): winsorize(1%,99%) → z-score → rank
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r"E:\QuantLab")
from factors.base import FactorBase


def _xs_score_row(values, low_better=False):
    """单截面 winsorize(1%,99%) → z-score → rank, 返回 [0,1] 得分 (越高越好)"""
    arr = np.asarray(values, dtype=float)
    valid = ~np.isnan(arr)
    if valid.sum() < 10:
        return np.full(len(arr), np.nan)
    x = arr.copy()
    v = x[valid]
    lo, hi = np.nanpercentile(v, [1, 99])
    x[valid] = np.clip(v, lo, hi)
    mu = np.nanmean(x)
    sd = np.nanstd(x)
    if sd == 0 or np.isnan(sd):
        return np.full(len(arr), np.nan)
    z = (x - mu) / sd
    z[~valid] = np.nan
    # rank 到 [0,1]; low_better 时取反 (低 ATR% 得高分)
    ranks = np.full(len(z), np.nan)
    valid_idx = np.where(valid)[0]
    sub = z[valid_idx]
    r = pd.Series(sub).rank(pct=True).values
    if low_better:
        r = 1.0 - r
    ranks[valid_idx] = r
    return ranks


class ATRFactor(FactorBase):
    """ATR(14)/close 低波动因子: 越低越好"""

    def __init__(self, window=14):
        super().__init__("atr_pct", "volatility", "ATR(%d)/close, 低波高分" % window)
        self.window = window

    def atr_pct_wide(self, panel):
        """计算 ATR% 原始值宽表 (index=date, columns=code)"""
        tr = np.maximum(
            panel["high"] - panel["low"],
            np.maximum((panel["high"] - panel["prev_close"]).abs(),
                       (panel["low"] - panel["prev_close"]).abs()),
        )
        tr_wide = tr.unstack("ts_code")
        atr = tr_wide.ewm(span=self.window, adjust=False).mean()
        close_wide = panel["close"].unstack("ts_code")
        return atr / close_wide

    def compute(self, panel, fin_ffill=None, **kwargs):
        """返回因子得分 Series, index=(date, code), 越高越好"""
        raw = self.atr_pct_wide(panel)
        vals = raw.values
        out = np.empty(vals.shape)
        for i in range(vals.shape[0]):
            out[i] = _xs_score_row(vals[i], low_better=True)
        wide = pd.DataFrame(out, index=raw.index, columns=raw.columns)
        return wide.stack()


class TurnoverFactor(FactorBase):
    """换手率适中性因子: 以 4.5% 为中枢的倒V型映射 (1-8% 区间最优)"""

    def __init__(self, center=4.5, half_width=4.5):
        super().__init__("turnover_mid", "liquidity",
                         "换手率倒U型映射, 中枢%.1f%%" % center)
        self.center = center
        self.half_width = half_width

    def raw_score_wide(self, panel):
        """倒V型原始映射宽表: score = max(0, 1 - |turnover - center| / half_width)"""
        to_wide = panel["turnover_rate"].unstack("ts_code")
        return np.maximum(0.0, 1.0 - (to_wide - self.center).abs() / self.half_width)

    def compute(self, panel, fin_ffill=None, **kwargs):
        """返回因子得分 Series, index=(date, code), 越高越好

        注意: panel 需含 turnover_rate 列 (百分比刻度, 如 3.5 表示 3.5%)。
        """
        raw = self.raw_score_wide(panel)
        vals = raw.values
        out = np.empty(vals.shape)
        for i in range(vals.shape[0]):
            out[i] = _xs_score_row(vals[i], low_better=False)
        wide = pd.DataFrame(out, index=raw.index, columns=raw.columns)
        return wide.stack()


def build_factor_frames(panel, names=("atr_rank", "turnover_rank")):
    """批量构建因子得分宽表 {name: DataFrame(index=date, columns=code)}

    供 V2Scorer.attach_factor() 使用。
    """
    frames = {}
    if "atr_rank" in names:
        atr_long = ATRFactor().compute(panel)
        frames["atr_rank"] = atr_long.unstack("ts_code")
    if "turnover_rank" in names:
        to_long = TurnoverFactor().compute(panel)
        frames["turnover_rank"] = to_long.unstack("ts_code")
    return frames
