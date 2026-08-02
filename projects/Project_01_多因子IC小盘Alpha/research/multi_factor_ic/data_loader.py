# coding=utf-8
"""加载 Parquet 数据，构建面板"""

import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

import pandas as pd
import numpy as np
from research.multi_factor_ic.config import (
    DAILY_PATH, BASIC_PATH, FINANCE_PATH,
    UNIVERSE_RANK_START, UNIVERSE_RANK_END,
    START_DATE, END_DATE,
)


def load_universe():
    """加载全量候选池（按市值排名前4000），用于build_panel构建宽面板。"""
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    latest_date = idx.get_level_values("trade_date").max()

    mask = idx.get_level_values("trade_date") == latest_date
    latest = daily.loc[mask].copy()
    latest["circ_mv"] = latest["circ_mv"].fillna(0)

    sorted_idx = latest["circ_mv"].sort_values(ascending=False).index
    selected = sorted_idx[:4000]
    codes = set(selected.get_level_values("ts_code"))
    return codes


def get_universe_at_date(panel, date):
    """获取 date 日在市值排名 [UNIVERSE_RANK_START, UNIVERSE_RANK_END) 区间内的股票。
    
    用于消除静态 universe 的生存偏差，实现动态滚动选股池。
    """
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    # 找到 <= date 的最新交易日
    valid = [d for d in trade_dates if d <= date]
    if not valid:
        return set()
    nearest = valid[-1]
    date_data = panel.loc[nearest]
    circ_mv = date_data["circ_mv"].fillna(0)
    ranked = circ_mv.sort_values(ascending=False)
    selected = ranked.iloc[UNIVERSE_RANK_START:UNIVERSE_RANK_END]
    return set(selected.index)


def load_finance_data(codes):
    """加载季度财务指标。"""
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[fin["ts_code"].isin(codes)].copy()

    fin = fin[["ts_code", "end_date", "roe", "grossprofit_margin",
               "netprofit_margin", "bps", "ocfps"]].copy()
    fin["end_date"] = pd.to_datetime(fin["end_date"], format="%Y%m%d")
    fin = fin.sort_values("ts_code")
    fin = fin.groupby(["ts_code", "end_date"], as_index=False).last()
    return fin


def build_panel(universe_codes):
    """构建面板数据 DataFrame(date, code) -> factor_values。
    
    使用扩大后的 universe_codes（全量候选）避免生存偏差。
    在回测阶段通过 get_universe_at_date 做动态日级过滤。
    """
    print("[data_loader] 加载日线数据...")
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index

    mask = idx.get_level_values("ts_code").isin(list(universe_codes))
    daily = daily.loc[mask].copy()
    idx = daily.index
    print(f"[data_loader] 按成分股过滤(宽池): {len(daily)} 行")

    trade_dates = idx.get_level_values("trade_date")
    start_ts = pd.Timestamp(START_DATE).date()
    end_ts = pd.Timestamp(END_DATE).date()
    date_mask = (trade_dates >= start_ts) & (trade_dates <= end_ts)
    daily = daily.loc[date_mask].copy()
    print(f"[data_loader] 按日期过滤: {len(daily)} 行")

    idx = daily.index
    panel = pd.DataFrame({
        "close": daily["close"].values,
        "open": daily["open"].values,
        "pe_ttm": daily["pe_ttm"].values,
        "pb": daily["pb"].values,
        "dv_ratio": daily["dv_ratio"].values,
        "turnover_rate": daily["turnover_rate"].values,
        "total_mv": daily["total_mv"].values,
        "circ_mv": daily["circ_mv"].values,
        "vol": daily["vol"].values,
        "amount": daily["amount"].values,
        "pct_chg": daily["pct_chg"].values,
    }, index=idx)

    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]
    print(f"[data_loader] 去ST/停牌后: {len(panel)} 行")

    print("[data_loader] 加载财务数据...")
    fin = load_finance_data(universe_codes)

    fin_pivot = fin.pivot_table(
        index="end_date", columns="ts_code",
        values=["roe", "grossprofit_margin", "netprofit_margin", "bps", "ocfps"],
    )

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    all_dates_idx = pd.DatetimeIndex(trade_dates)
    fin_ffill = fin_pivot.reindex(all_dates_idx, method="ffill")

    print(f"[data_loader] 面板构建完成: {panel.shape}")
    return panel, fin_ffill


def get_rebalance_dates(panel, freq="M"):
    """获取调仓日列表。

    Args:
        freq: "M"=月末, "2M"=双月末, "W"=每周五, "2W"=每两周, "Q"=季末
    Returns: [datetime.date, ...]
    """
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    s = pd.Series(pd.DatetimeIndex(trade_dates))
    if freq == "W":
        idx = s.groupby([s.dt.year, s.dt.isocalendar().week]).idxmax()
    elif freq == "2W":
        idx = s.groupby([s.dt.year, s.dt.isocalendar().week // 2]).idxmax()
    elif freq == "M":
        idx = s.groupby(s.dt.to_period("M")).idxmax()
    elif freq == "2M":
        idx = s.groupby(s.dt.year * 24 + s.dt.month // 2).idxmax()
    elif freq == "Q":
        idx = s.groupby(s.dt.to_period("Q")).idxmax()
    else:
        raise ValueError(f"不支持调仓频率: {freq}")
    return [d.date() for d in s.iloc[idx].tolist()]


def get_monthly_rebalance_dates(panel):
    """兼容旧接口。"""
    return get_rebalance_dates(panel, freq="M")
