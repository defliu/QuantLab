# coding=utf-8
"""因子计算函数。"""

import numpy as np
import pandas as pd


def winsorize(series, lower=0.01, upper=0.99):
    lo = series.quantile(lower)
    hi = series.quantile(upper)
    return series.clip(lo, hi)


def standardize(series):
    return (series - series.mean()) / series.std(ddof=0)


FACTOR_CONFIG = {
    "EP": {"category": "价值", "params": {}},
    "BP": {"category": "价值", "params": {}},
    "dividend_yield": {"category": "价值", "params": {}},
    "ROE": {"category": "质量", "params": {}},
    "grossprofit_margin": {"category": "质量", "params": {}},
    "momentum_1m": {"category": "动量", "params": {"window": 20}},
    "momentum_3m": {"category": "动量", "params": {"window": 60}},
    "momentum_6m": {"category": "动量", "params": {"window": 120}},
    "turnover_change": {"category": "情绪", "params": {}},
    "volatility_60d": {"category": "情绪", "params": {"window": 60}},
    "liquidity_avg": {"category": "情绪", "params": {"window": 20}},
    "vwap_volume_corr": {"category": "量价", "params": {"window": 5}},
}


def compute_all_factors(panel, fin_ffill, date):
    """计算 date 日所有因子的截面值。返回 {factor_name: Series}"""
    result = {}
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    date_series = panel.loc[date]

    # EP
    ep = 1.0 / date_series["pe_ttm"].replace(0, np.nan)
    result["EP"] = ep

    # BP
    bp = 1.0 / date_series["pb"].replace(0, np.nan)
    result["BP"] = bp

    # dividend_yield
    result["dividend_yield"] = date_series["dv_ratio"]

    # ROE (从财务表取最近季度, 考虑财报披露滞后45天)
    fin_dates = fin_ffill.index
    lookup_date = pd.Timestamp(date) - pd.Timedelta(days=45)
    valid = fin_dates[fin_dates <= lookup_date]
    if len(valid) > 0:
        roe = fin_ffill.loc[valid[-1], "roe"]
        gpm = fin_ffill.loc[valid[-1], "grossprofit_margin"]
    else:
        roe = pd.Series(np.nan, index=date_series.index)
        gpm = pd.Series(np.nan, index=date_series.index)
    result["ROE"] = roe.reindex(date_series.index)
    result["grossprofit_margin"] = gpm.reindex(date_series.index)

    # 动量（排除当日：使用 date-1 的收盘价）
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

    # 换手率变化 (近20d均值 / 近60d均值, 排除当日)
    if date_idx > 60:
        s20 = panel.loc[trade_dates[date_idx - 20]:prev_date, "turnover_rate"].groupby("ts_code").mean()
        s60 = panel.loc[trade_dates[date_idx - 60]:prev_date, "turnover_rate"].groupby("ts_code").mean()
        tc = s20 / s60.replace(0, np.nan) - 1.0
        result["turnover_change"] = tc.reindex(date_series.index)
    else:
        result["turnover_change"] = pd.Series(0.0, index=date_series.index)

    # 波动率 (60d, 排除当日)
    if date_idx > 60:
        pct = panel.loc[trade_dates[date_idx - 60]:prev_date, "pct_chg"]
        vol = pct.groupby("ts_code").std()
        result["volatility_60d"] = vol.reindex(date_series.index)
    else:
        result["volatility_60d"] = pd.Series(0.0, index=date_series.index)

    # 流动性 (20d avg log amount, 排除当日)
    if date_idx > 20:
        amt = panel.loc[trade_dates[date_idx - 20]:prev_date, "amount"]
        la = np.log(amt.groupby("ts_code").mean().replace(0, np.nan))
        result["liquidity_avg"] = la.reindex(date_series.index)
    else:
        result["liquidity_avg"] = pd.Series(0.0, index=date_series.index)

    # VWAP量价相关: -rank(5d Spearman corr(rank(VWAP), rank(vol)))
    # 对应 gtja191_090: -1 * rank(corr(rank(vwap), rank(volume), 5))
    # VWAP = amount(元) / (volume(手) × 100) = 元/股
    # 使用向量化 unstack + rolling corr 避免慢速 groupby-apply
    if date_idx >= 5:
        start = trade_dates[date_idx - 4]
        amount_wide = panel.loc[start:date, "amount"].unstack("ts_code")
        vol_wide = panel.loc[start:date, "vol"].unstack("ts_code")
        # 边界处理: volume=0(停牌)或amount=0(无交易)的股票排除
        # 5日内任意一天volume/amount为零或NaN → 整只股票不参与计算
        safe_mask = (vol_wide > 0).all(axis=0) & (amount_wide > 0).all(axis=0)
        vol_wide = vol_wide.loc[:, safe_mask]
        amount_wide = amount_wide.loc[:, safe_mask]
        if vol_wide.shape[1] == 0:
            # 全部被排除（全市场停牌日，极罕见）
            result["vwap_volume_corr"] = pd.Series(0.0, index=date_series.index)
        else:
            vwap_wide = amount_wide / (vol_wide * 100.0)
            # 沿时间轴 rank（每个股票独立 rank）
            vwap_rank = vwap_wide.rank(axis=0)
            vol_rank = vol_wide.rank(axis=0)
            # 5日滚动 Spearman corr: rolling corr 等价于 Pearson on ranks
            corr_wide = vwap_rank.rolling(5, min_periods=5).corr(vol_rank)
            if len(corr_wide) > 0:
                latest_corr = corr_wide.iloc[-1]
                result["vwap_volume_corr"] = (-latest_corr.rank()).reindex(date_series.index)
            else:
                result["vwap_volume_corr"] = pd.Series(0.0, index=date_series.index)
            # 新股/停牌等导致的缺失值填0（中性），不参与评分排序
            if result["vwap_volume_corr"].isna().any():
                result["vwap_volume_corr"] = result["vwap_volume_corr"].fillna(0.0)
    else:
        # 前4个交易日数据不足5天，所有股票填0（中性）
        result["vwap_volume_corr"] = pd.Series(0.0, index=date_series.index)

    return result
