# coding: utf-8
"""OMD 马尔可夫排名链选股（T-20260829-003，A股迁移版，long-only）。

报告 #3：用 trailing return 排名 + trailing vol 排名两条马尔可夫链，经验转移矩阵
估计 p^R（下期落入最高收益档的概率）、p^V（下期落入最低波动档的概率），
score = (1-λ)p^R + λ p^V，持有 score 最高的 N 只（剥离空头腿，纯 long-only）。

实现要点：
  * 无条件转移矩阵（不引入 logit/协变量），pandas 一次性出原型。
  * walk-forward：第 t 月调仓用的转移矩阵仅由 t 之前的历史估计。
  * 重计算在模块加载时完成，结果存 _SCHEDULE；evaluate_day 仅在调仓日返回缓存权重。
  * 关键先验已同时打印：波动率排名"一步可预测"命中率（决定是否继续投入）。
"""
from strategy.registry import register_strategy

import os
import numpy as np
import pandas as pd

ALLOWED_TRADING_MODELS = ["next_open"]

_PARQUET = "D:/astock/daily/stock_daily.parquet"
_K = 10                 # 排名档数
_L = 12                 # trailing 窗口（月）
_N_HOLD = 100           # 持有数量
_LAMBDA = 0.5           # vol 权重
_WARMUP_MONTHS = 24     # 至少需要多少月历史才开始调仓

_SCHEDULE = {}          # rebalance_date(str) -> {code: weight}
_PRIOR_VOL_HIT = None   # 波动率排名一步可预测命中率
_PRIOR_RET_HIT = None


def _load_monthly():
    import pyarrow.parquet as pq
    cols = ["ts_code", "trade_date", "close", "adj_factor"]
    present = set(pq.ParquetFile(_PARQUET).schema_arrow.names)
    cols = [c for c in cols if c in present]
    t = pq.read_table(_PARQUET, columns=cols).to_pandas()
    if "trade_date" not in t.columns:
        t = t.reset_index()
    t["trade_date"] = pd.to_datetime(t["trade_date"])
    if "adj_factor" in t.columns:
        t["close"] = t["close"].astype(float) * t["adj_factor"].astype(float)
    t = t[["ts_code", "trade_date", "close"]].copy()
    # 月频：取每月最后一个交易日
    t["ym"] = t["trade_date"].dt.to_period("M")
    t = t.sort_values(["ts_code", "trade_date"])
    idx = t.groupby(["ts_code", "ym"])["trade_date"].idxmax()
    m = t.loc[idx].copy()
    m["close"] = m["close"].astype(float)
    # pivot: index=month(period), columns=ts_code, values=close
    wide = m.pivot_table(index="ym", columns="ts_code", values="close").sort_index()
    # 每月最后一个实际交易日（用于把调度映射到引擎交易日历）
    month_end = m.groupby("ym")["trade_date"].max()
    month_end_map = {str(k): v.strftime("%Y-%m-%d") for k, v in month_end.items()}
    return wide, month_end_map


def _bin_ranks(series_row):
    """把某月的 cross-sectional 值分 K 档（NaN -> -1）。"""
    vals = series_row.values.astype(float)
    out = np.full(len(vals), -1, dtype=np.int64)
    mask = ~np.isnan(vals)
    if mask.sum() < _K:
        return out
    # rank 1..K
    order = np.argsort(np.argsort(vals[mask]))
    bins = np.floor(order / (mask.sum() / float(_K))).astype(int)
    bins = np.clip(bins, 0, _K - 1)
    out[mask] = bins
    return out


def _precompute():
    global _SCHEDULE, _PRIOR_VOL_HIT, _PRIOR_RET_HIT
    wide, month_end_map = _load_monthly()
    months = list(wide.index)
    codes = list(wide.columns)
    cidx = {c: i for i, c in enumerate(codes)}
    n = len(codes)
    closes = wide.values.astype(float)  # months x codes
    mret = np.full_like(closes, np.nan)
    mret[1:] = closes[1:] / closes[:-1] - 1.0
    # trailing return / vol over L months
    tr_ret = np.full_like(closes, np.nan)
    tr_vol = np.full_like(closes, np.nan)
    for i in range(_L, len(months)):
        win = mret[i - _L:i]
        tr_ret[i] = np.nanprod(1.0 + win, axis=0) - 1.0
        tr_vol[i] = np.nanstd(win, axis=0)

    # 逐月分档
    ret_bins = np.full_like(closes, -1, dtype=np.int64)
    vol_bins = np.full_like(closes, -1, dtype=np.int64)
    for i in range(len(months)):
        ret_bins[i] = _bin_ranks(pd.Series(tr_ret[i]))
        vol_bins[i] = _bin_ranks(pd.Series(tr_vol[i]))

    # 向量化转移计数：用 bincount 把 (prev,cur) 直方图一次算出来
    def _acc_trans(prev, cur):
        valid = (prev >= 0) & (cur >= 0)
        idx = prev[valid] * _K + cur[valid]
        cnt = np.bincount(idx, minlength=_K * _K).astype(np.float64)
        return cnt.reshape(_K, _K)

    # 单次遍历：累积转移矩阵（反映 < s），用于命中率与 walk-forward 调度，
    # 循环末尾再把第 s 月的转移累加进去。
    cum_R = np.zeros((_K, _K), dtype=np.float64)
    cum_V = np.zeros((_K, _K), dtype=np.float64)
    vol_hit = 0.0
    vol_tot = 0.0
    ret_hit = 0.0
    ret_tot = 0.0
    first_rebal = _L + _WARMUP_MONTHS

    for s in range(1, len(months)):
        rb_prev = ret_bins[s - 1]
        rb_cur = ret_bins[s]
        vb_prev = vol_bins[s - 1]
        vb_cur = vol_bins[s]

        # ---- 命中率（用截至 s-1 的累积矩阵，即当前 cum_R）----
        if s >= _L + 1:
            Rr = cum_R / (cum_R.sum(axis=1, keepdims=True) + 1e-12)
            Vr = cum_V / (cum_V.sum(axis=1, keepdims=True) + 1e-12)
            cp = ret_bins[s - 1]
            cv = vol_bins[s - 1]
            nxt_r = ret_bins[s]
            nxt_v = vol_bins[s]
            valid = (cp >= 0) & (nxt_r >= 0) & (cv >= 0) & (nxt_v >= 0)
            if valid.any():
                # 当前档 -> 最可能下一档（行内 argmax），随机基线应为 1/K=0.10
                pred_r = Rr.argmax(axis=1)[cp[valid]]
                pred_v = Vr.argmax(axis=1)[cv[valid]]
                ret_hit += int((pred_r == nxt_r[valid]).sum())
                ret_tot += int(valid.sum())
                vol_hit += int((pred_v == nxt_v[valid]).sum())
                vol_tot += int(valid.sum())

        # ---- walk-forward 调度（同样用 < s 的矩阵）----
        if s >= first_rebal:
            Rr = cum_R / (cum_R.sum(axis=1, keepdims=True) + 1e-12)
            Vr = cum_V / (cum_V.sum(axis=1, keepdims=True) + 1e-12)
            cp = ret_bins[s]
            cv = vol_bins[s]
            valid = (cp >= 0) & (cv >= 0)
            if valid.sum() > 0:
                pr = Rr[cp[valid], _K - 1]   # 最高收益档
                pv = Vr[cv[valid], 0]          # 最低波动档
                scores = np.full(n, np.nan)
                scores[valid] = (1.0 - _LAMBDA) * pr + _LAMBDA * pv
                order = np.argsort(scores[valid])[::-1]
                sel = np.where(valid)[0][order[:min(_N_HOLD, valid.sum())]]
                w = {codes[j]: 1.0 for j in sel}
                _SCHEDULE[month_end_map[str(months[s])]] = w

        # ---- 累加第 s 月转移 ----
        cum_R += _acc_trans(rb_prev, rb_cur)
        cum_V += _acc_trans(vb_prev, vb_cur)

    if vol_tot > 0:
        _PRIOR_VOL_HIT = vol_hit / vol_tot
    if ret_tot > 0:
        _PRIOR_RET_HIT = ret_hit / ret_tot
    print("[OMD] prior vol-rank 1-step hit=%.3f  ret-rank 1-step hit=%.3f  schedule months=%d"
          % (_PRIOR_VOL_HIT, _PRIOR_RET_HIT, len(_SCHEDULE)))


_precompute()


@register_strategy("omd_markov")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    weights = _SCHEDULE.get(current_date)
    if weights is None:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": dict(weights),
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(weights),
            "candidate_passed": len(weights),
            "strategy_specific": {
                "omd_markov": {
                    "vol_prior_hit": round(float(_PRIOR_VOL_HIT or 0), 4),
                    "ret_prior_hit": round(float(_PRIOR_RET_HIT or 0), 4),
                },
            },
        },
        "logs": ["%s rebalance: %d selected" % (current_date, len(weights))],
    }
