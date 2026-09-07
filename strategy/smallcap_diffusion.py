# coding: utf-8
"""小市值 + 扩散指数择时（T-20260829-004，A股迁移版）。

报告 #5：持有全市场市值最小 N 只（N=200），用"微盘200中近20日上涨占比"扩散指数 DI
判断微盘行情参与度：
    MA(DI,10) > MA(DI,20) -> 满仓；否则空仓
另支持日历过滤（1/4月强制空仓）作敏感性测试（cfg: jan_apr_exit=1）。

调度表在模块加载时预计算；evaluate_day 仅在状态切换/月度重选日返回目标权重。
"""
from strategy.registry import register_strategy

import numpy as np
import pandas as pd

ALLOWED_TRADING_MODELS = ["next_open"]

_PARQUET = "D:/astock/daily/stock_daily.parquet"
_N = 200
_MIN_HISTORY = 250
_WARMUP_DAYS = 60   # DI 均线需要前期
_JAN_APR_EXIT = False  # 由 set_params 覆盖

_SCHEDULE = {}       # date(str) -> {code: weight} 或 {}（空仓）
_UNIVERSE_MIN_DATE = "2009-01-01"


def set_params(n=200, jan_apr_exit=False):
    global _N, _JAN_APR_EXIT
    _N = n
    _JAN_APR_EXIT = jan_apr_exit


def _precompute():
    global _SCHEDULE
    import pyarrow.parquet as pq
    cols = ["ts_code", "trade_date", "close", "adj_factor", "circ_mv"]
    present = set(pq.ParquetFile(_PARQUET).schema_arrow.names)
    cols = [c for c in cols if c in present]
    t = pq.read_table(_PARQUET, columns=cols).to_pandas()
    if "trade_date" not in t.columns:
        t = t.reset_index()
    t["trade_date"] = pd.to_datetime(t["trade_date"])
    if "adj_factor" in t.columns:
        t["close"] = t["close"].astype(float) * t["adj_factor"].astype(float)
    t["circ_mv"] = t["circ_mv"].astype(float)
    t = t.sort_values(["ts_code", "trade_date"])

    # 全局交易日历
    all_dates = pd.to_datetime(sorted(t["trade_date"].unique()))
    date_str = [d.strftime("%Y-%m-%d") for d in all_dates]
    date_to_i = {d: i for i, d in enumerate(date_str)}

    # 每只股票：date->close 映射（仅保留有 circ_mv 过滤用；close 用于收益）
    codes = sorted(t["ts_code"].unique())
    close_map = {}
    mv_map = {}     # month_end_date -> {code: circ_mv}
    for c, g in t.groupby("ts_code"):
        g = g.sort_values("trade_date")
        dts = g["trade_date"].dt.strftime("%Y-%m-%d").tolist()
        cls = g["close"].astype(float).tolist()
        close_map[c] = (dts, cls)
    # circ_mv 月末快照
    t["ym"] = t["trade_date"].dt.to_period("M")
    for (ym, c), g in t.groupby(["ym", "ts_code"]):
        # 月内最后一个交易日
        last = g.sort_values("trade_date").iloc[-1]
        mv_map.setdefault(str(last["trade_date"].date()), {})[c] = float(last["circ_mv"])

    # 逐月选取 micro-N，并逐日算 DI / 状态
    months = sorted({d[:7] for d in date_str if d >= _UNIVERSE_MIN_DATE})
    schedule = {}
    prev_state = False
    di_hist = []
    cur_micro = []
    cur_month = None
    for di_i, d in enumerate(date_str):
        if d < _UNIVERSE_MIN_DATE:
            continue
        ym = d[:7]
        if ym != cur_month:
            cur_month = ym
            # 选 micro-N：用本月末（或最近可用）circ_mv 快照
            snap = None
            for back in range(0, 40):
                cand = (pd.Timestamp(d) - pd.Timedelta(days=back)).strftime("%Y-%m-%d")
                if cand in mv_map and mv_map[cand]:
                    snap = mv_map[cand]
                    break
            if snap:
                vals = [(c, v) for c, v in snap.items() if v > 0]
                vals.sort(key=lambda x: x[1])
                cur_micro = [c for c, _ in vals[:_N]]
            else:
                cur_micro = []
        if di_i < _WARMUP_DAYS or len(cur_micro) < 50:
            di_hist.append(np.nan)
            continue
        if di_i < 20:
            di_hist.append(np.nan)
            continue
        up = 0
        tot = 0
        for c in cur_micro:
            dts, cls = close_map[c]
            # 用 bisect 找 d 和 d-20 的位置
            import bisect
            i = bisect.bisect_left(dts, d)
            if i >= len(dts) or dts[i] != d:
                continue
            j = bisect.bisect_left(dts, date_str[di_i - 20])
            if j >= len(dts):
                continue
            c0 = cls[j]
            c1 = cls[i]
            if c0 > 0:
                tot += 1
                if c1 > c0:
                    up += 1
        di = (up / tot) if tot > 0 else np.nan
        di_hist.append(di)
        if di_i < _WARMUP_DAYS + 20 or np.isnan(di):
            continue
        # MA10 / MA20 of DI
        window = di_hist[-20:]
        if len(window) < 20:
            continue
        ma10 = np.nanmean(di_hist[-10:]) if len(di_hist) >= 10 else np.nan
        ma20 = np.nanmean(window)
        if np.isnan(ma10) or np.isnan(ma20):
            continue
        # 日历过滤
        month_num = int(d[5:7])
        cal_block = _JAN_APR_EXIT and month_num in (1, 4)
        state = (ma10 > ma20) and (not cal_block)
        # 状态切换或月度重选（在市且换月）-> 发信号
        reselect_month = (d[8:10] == "01")  # 简化：每月1号附近重选（用月初首个交易日近似）
        if state and (not prev_state or reselect_month):
            w = {c: 1.0 for c in cur_micro}
            schedule[d] = w
        elif (not state) and prev_state:
            schedule[d] = {}  # 空仓
        prev_state = state

    _SCHEDULE = schedule


_precompute()


@register_strategy("smallcap_diffusion")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    w = _SCHEDULE.get(current_date)
    if w is None:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": dict(w),
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(w),
            "candidate_passed": len(w),
            "strategy_specific": {
                "smallcap_diffusion": {"jan_apr_exit": bool(_JAN_APR_EXIT)},
            },
        },
        "logs": ["%s rebalance: %d selected" % (current_date, len(w))],
    }
