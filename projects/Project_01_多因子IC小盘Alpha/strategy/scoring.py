# coding=utf-8
"""多因子评分模型 — BP + 反转 + 低波 + ROE + VWAP量价。

权重配置:
  BP: 27%, 反转: 22.5%, 低波: 22.5%, ROE: 18%, VWAP量价: 10%
"""

import numpy as np
import pandas as pd
from factors.base import FactorBase


# 权重配置
FACTOR_WEIGHTS = {
    "BP": 0.27,
    "reversal_1m": 0.225,
    "volatility_60d": 0.225,
    "ROE": 0.18,
    "vwap_volume_corr": 0.10,
}


def normalize(series, reverse=False):
    """winsorize(1%,99%) → z-score → 方向控制"""
    s = series.copy()
    lo = s.quantile(0.01)
    hi = s.quantile(0.99)
    s = s.clip(lo, hi)
    s = (s - s.mean()) / s.std(ddof=0)
    if reverse:
        s = -s
    return s


def compute_all_factors(panel, fin_ffill, date):
    """计算 date 日所有因子的截面值。

    Args:
        panel: MultiIndex(trade_date, ts_code) DataFrame
        fin_ffill: 财务数据宽表
        date: 目标日期

    Returns:
        dict: {factor_name: Series(ts_code)}
    """
    result = {}
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    date_series = panel.loc[date]

    # EP
    ep = 1.0 / date_series["pe_ttm"].replace(0, np.nan)
    result["EP"] = ep

    # BP
    bp = 1.0 / date_series["pb"].replace(0, np.nan)
    result["BP"] = bp

    # ROE (财报披露滞后45天)
    fin_dates = fin_ffill.index
    lookup_date = pd.Timestamp(date) - pd.Timedelta(days=45)
    valid = fin_dates[fin_dates <= lookup_date]
    if len(valid) > 0:
        roe = fin_ffill.loc[valid[-1], "roe"]
    else:
        roe = pd.Series(np.nan, index=date_series.index)
    result["ROE"] = roe.reindex(date_series.index)

    # 动量 (排除当日)
    date_idx = trade_dates.index(date)
    prev_idx = max(0, date_idx - 1)
    prev_date = trade_dates[prev_idx]
    prev_close = panel.loc[prev_date, "close"]
    for name, w in [("momentum_1m", 20), ("momentum_3m", 60), ("momentum_6m", 120)]:
        if date_idx >= w:
            start = trade_dates[date_idx - w]
            start_close = panel.loc[start, "close"]
            common = prev_close.index.intersection(start_close.index)
            ret = prev_close[common] / start_close[common] - 1.0
            result[name] = ret.reindex(date_series.index)
        else:
            result[name] = pd.Series(0.0, index=date_series.index)

    # 波动率 (60d)
    if date_idx > 60:
        pct = panel.loc[trade_dates[date_idx - 60]:prev_date, "pct_chg"]
        vol = pct.groupby("ts_code").std()
        result["volatility_60d"] = vol.reindex(date_series.index)
    else:
        result["volatility_60d"] = pd.Series(0.0, index=date_series.index)

    # VWAP量价相关
    from factors.vwap_volume_corr import compute_single
    result["vwap_volume_corr"] = compute_single(panel, date, trade_dates, date_series)

    return result


def score(panel, fin_ffill, date, filter_func=None, weights=None):
    """计算 date 日综合评分 (0~100)。

    Args:
        filter_func: callable(panel, fin_ffill, date) -> mask
        weights: 自定义权重, None=默认

    Returns:
        pd.Series: 评分 (0~100), index=ts_code
    """
    raw = compute_all_factors(panel, fin_ffill, date)

    # 基础安全过滤
    date_data = panel.loc[date]
    idx = date_data.index
    base_mask = (date_data["pe_ttm"] > 0) & (date_data["pb"] > 0)

    fin_dates = fin_ffill.index
    lookup_date = pd.Timestamp(date) - pd.Timedelta(days=45)
    valid = fin_dates[fin_dates <= lookup_date]
    if len(valid) > 0:
        roe_filter = fin_ffill.loc[valid[-1], "roe"].reindex(idx, fill_value=-np.inf)
        base_mask = base_mask & (roe_filter >= -20)

    # 叠加自定义过滤
    if filter_func is not None:
        cap_mask = filter_func(panel, fin_ffill, date)
        if isinstance(cap_mask, pd.Series):
            cap_mask = cap_mask.reindex(idx, fill_value=False)
        else:
            cap_mask = pd.Series(cap_mask, index=idx).fillna(False)
    else:
        cap_mask = pd.Series(True, index=idx)

    final_mask = base_mask & cap_mask

    for name in raw:
        raw[name] = raw[name].where(final_mask, other=np.nan)

    # 各子因子归一化
    sub_scores = {}
    sub_scores["BP"] = normalize(raw["BP"], reverse=False)
    sub_scores["reversal_1m"] = normalize(raw["momentum_1m"], reverse=True)
    sub_scores["volatility_60d"] = normalize(raw["volatility_60d"], reverse=True)
    sub_scores["ROE"] = normalize(raw["ROE"], reverse=False)
    sub_scores["vwap_volume_corr"] = normalize(raw["vwap_volume_corr"], reverse=False)

    # 加权合成
    active_weights = weights if weights is not None else FACTOR_WEIGHTS
    total = pd.Series(np.nan, index=idx)
    weight_sum = 0.0
    for name, w in active_weights.items():
        s = sub_scores.get(name)
        if s is not None and len(s.dropna()) > 0:
            total = total.add(s * w, fill_value=0)
            weight_sum += w

    if weight_sum > 0:
        total = total / weight_sum * 100.0
    return total
