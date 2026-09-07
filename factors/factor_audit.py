# -*- coding: utf-8 -*-
"""
factor_audit.py —— A股事件型因子审计工具（V2 口径）
====================================================
来源：2026-09-05 对「60线归位战法」的审计实践。该次审计中，基准口径错误
一度伪造出"小市值域 +5.30%、t=5.66"的强显著假象，改用买入并持有基准后坍塌为
+0.34%、t=0.37。本模块把正确的口径固化为默认行为，避免重复踩坑。

核心原则（务必遵守）
--------------------
1. **买入并持有基准**：个股怎么持有，基准就怎么持有。
   禁止用"每日调仓的池内等权净值 cumprod"作为个股事件基准 ——
   分层池（尤其市值分位池）成员每日变动，上涨个股升出池外，
   基准被迫截断其后续收益（成分漂移/再平衡偏差），池子越小偏差越大。
2. **分层结论必须在该层内部做随机对照**：先看该层随机抽签能拿多少。
   若随机值本身远离 0，说明基准有偏，t 值再大也无意义。
3. **止损必须同步施加于随机对照组**：否则只是截断左尾，制造"改善"假象。
4. 判定门槛：真实信号须位于随机分布 **95 百分位**以上。

典型用法
--------
    from factor_audit import load_panel, quick_audit
    panel = load_panel()
    res = quick_audit(panel, signal=my_signal_matrix,   # bool DataFrame, 宽表对齐
                      entry=3, holds=[20, 60, 120], stops=[0.0, 0.08])
    print(res.verdict)      # PASS / FAIL
    res.table              # 各持有期×止损的超额、t值、随机分位
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

DEFAULT_SRC = "D:/astock/daily/stock_daily.parquet"
MIN_LISTED = 250
MIN_AMOUNT = 50_000      # 千元 = 5000 万元
LIQ_WIN = 20


@dataclass
class Panel:
    """宽表面板（index=trade_date, columns=ts_code）。"""
    close: pd.DataFrame      # 前复权收盘
    low: pd.DataFrame        # 前复权最低
    vol: pd.DataFrame
    amount: pd.DataFrame
    circ_mv: pd.DataFrame
    univ: pd.DataFrame       # 合格池 bool
    dates: pd.DatetimeIndex = field(init=False)
    codes: pd.Index = field(init=False)

    def __post_init__(self):
        self.dates = self.close.index
        self.codes = self.close.columns


def load_panel(src: str = DEFAULT_SRC,
               start: str = "2010-01-01",
               min_listed: int = MIN_LISTED,
               min_amount: float = MIN_AMOUNT,
               with_low: bool = True) -> Panel:
    """载入日线面板并构建合格池。合格池 = 非ST ∧ 上市≥N日 ∧ 20日均额≥阈值 ∧ 有价。"""
    cols = ["close", "vol", "amount", "adj_factor", "is_st", "listed_days", "circ_mv"]
    if with_low:
        cols.append("low")
    df = pd.read_parquet(src, columns=cols).reset_index()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df[df["trade_date"] >= start]
    df["adj_close"] = df["close"] * df["adj_factor"]
    if with_low:
        df["adj_low"] = df["low"] * df["adj_factor"]

    piv = lambda v: df.pivot(index="trade_date", columns="ts_code", values=v).sort_index()
    close, vol, amt = piv("adj_close"), piv("vol"), piv("amount")
    st, lis, mv = piv("is_st"), piv("listed_days"), piv("circ_mv")
    low = piv("adj_low") if with_low else pd.DataFrame(np.nan, close.index, close.columns)

    liq = amt.rolling(LIQ_WIN, min_periods=15).mean()
    univ = (st.fillna(1) == 0) & (lis >= min_listed) & (liq >= min_amount) & close.notna()
    return Panel(close=close, low=low, vol=vol, amount=amt, circ_mv=mv, univ=univ)


# ----------------------------------------------------------------- 基准
def buy_hold_benchmark(close: pd.DataFrame, pool: pd.DataFrame,
                       holds: Sequence[int]) -> Dict[int, np.ndarray]:
    """
    买入并持有基准：对每个交易日 d，取 d 日池内成员等权买入、持有 h 天的平均收益。
    返回 {h: array(len(dates))}，array[d] 即 d 日入场的基准收益。

    这是事件研究的**正确**基准。与之相对的 daily_rebalance_benchmark 仅在
    策略与基准同为日频组合（净值曲线对比）时才可使用。
    """
    c = close.to_numpy()
    p = pool.to_numpy()
    maxd = len(close)
    out = {}
    for h in holds:
        fwd = np.full((maxd, c.shape[1]), np.nan)
        fwd[:maxd - h] = c[h:] / c[:maxd - h] - 1
        out[h] = np.nanmean(np.where(p, fwd, np.nan), axis=1)
    return out


def daily_rebalance_benchmark(close: pd.DataFrame, pool: pd.DataFrame) -> pd.Series:
    """
    每日调仓的池内等权日收益序列 —— 仅用于**组合净值曲线**对比
    （策略与基准同为日频复利，口径一致）。
    切勿用于个股事件研究，否则引入成分漂移偏差。
    """
    ret = (close / close.shift(1) - 1).where(pool)
    return ret.mean(axis=1, skipna=True)


def market_cap_tiers(panel: Panel, q: Sequence[float] = (0.3, 0.7)) -> Dict[str, pd.DataFrame]:
    """按流通市值分位切池，返回 {'small': mask, 'mid': ..., 'big': ...}。"""
    pct = panel.circ_mv.where(panel.univ).rank(axis=1, pct=True)
    if len(q) == 2:
        return {
            "small": (pct <= q[0]) & panel.univ,
            "mid": (pct > q[0]) & (pct <= q[1]) & panel.univ,
            "big": (pct > q[1]) & panel.univ,
        }
    raise ValueError("q 需为两个分位点")


# ----------------------------------------------------------------- 事件研究
def _stopped_ret(close_np, low_np, entry_d, c_idx, h, stop):
    """带止损的持有期收益：窗口内最低价触及 entry*(1-stop) 则以止损价离场。"""
    p0 = close_np[entry_d, c_idx]
    ret = close_np[entry_d + h, c_idx] / p0 - 1
    if stop and stop > 0:
        lows = low_np[entry_d[:, None] + np.arange(1, h + 1),
                      np.repeat(c_idx[:, None], h, axis=1)]
        hit = np.nanmin(lows, axis=1) <= p0 * (1 - stop)
        ret = np.where(hit, -stop, ret)
    return ret


@dataclass
class AuditResult:
    table: pd.DataFrame
    verdict: str
    best_pct: float
    n_events: int

    def __repr__(self):
        return (f"<AuditResult verdict={self.verdict} n={self.n_events} "
                f"best_pct={self.best_pct:.1%}>")


def quick_audit(panel: Panel,
                signal: pd.DataFrame,
                holds: Sequence[int] = (20, 60, 120),
                stops: Sequence[float] = (0.0,),
                entry: int = 0,
                pool: Optional[pd.DataFrame] = None,
                R: int = 300,
                seed: int = 20260905,
                min_events: int = 30) -> AuditResult:
    """
    一站式审计：买入并持有基准 + 止损 + 随机对照。

    signal : bool DataFrame，与 panel.close 同形；True 表示 T 日发出信号。
    entry  : 信号后第几个交易日入场（如需"三日确认"则设为 3）。
    R      : 随机对照轮数。随机组施加**完全相同**的止损与持有期。
    """
    pool = panel.univ if pool is None else (pool & panel.univ)
    close_np = panel.close.to_numpy()
    low_np = panel.low.to_numpy()
    pool_np = pool.to_numpy()
    maxd, nC = close_np.shape
    col_idx = np.arange(nC)
    rng = np.random.default_rng(seed)

    bh_day = buy_hold_benchmark(panel.close, pool, list(holds))

    sig_np = signal.to_numpy() & pool_np
    d_idx, c_idx = np.nonzero(sig_np)
    ed = d_idx + entry
    keep = (ed + max(holds)) < maxd
    d_idx, c_idx, ed = d_idx[keep], c_idx[keep], ed[keep]
    if len(d_idx) < min_events:
        raise ValueError(f"事件数不足：{len(d_idx)} < {min_events}")

    cnt = np.bincount(d_idx, minlength=maxd)
    sdays = np.nonzero(cnt > 0)[0]
    picks = []
    for _ in range(R):
        picks.append([(d, rng.choice(col_idx[pool_np[d]],
                                     size=min(cnt[d], int(pool_np[d].sum())),
                                     replace=False))
                      for d in sdays if pool_np[d].any()])

    rows = []
    for h in holds:
        bmk = bh_day[h][ed]
        for s in stops:
            r = _stopped_ret(close_np, low_np, ed, c_idx, h, s) - bmk
            r = r[np.isfinite(r)]
            bm = np.array([
                np.concatenate([
                    _stopped_ret(close_np, low_np, np.array([d + entry]), pk, h, s)
                    - bh_day[h][d + entry] for d, pk in per
                ]).mean() for per in picks
            ])
            bm = bm[np.isfinite(bm)]
            rows.append({
                "hold": h, "stop": s, "n": len(r),
                "exc": float(r.mean()),
                "t": float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))),
                "win": float((r > 0).mean()),
                "boot": float(bm.mean()),
                "diff": float(r.mean() - bm.mean()),
                "pct": float((bm < r.mean()).mean()),
            })
    table = pd.DataFrame(rows)
    best = table["pct"].max()
    return AuditResult(table=table, verdict="PASS" if best >= 0.95 else "FAIL",
                       best_pct=float(best), n_events=int(len(d_idx)))


def param_scan(panel: Panel,
               signal_fn: Callable[[int], pd.DataFrame],
               params: Iterable[int],
               **kw) -> pd.DataFrame:
    """
    参数敏感性扫描。signal_fn(N) -> bool DataFrame。
    有效信号应在参数轴上出现"高原"；若全区间无正值或仅单点突出，判定为无效/过拟合。
    """
    rows = []
    for p in params:
        try:
            res = quick_audit(panel, signal_fn(p), **kw)
            row = res.table.iloc[0].to_dict()
            row.update({"param": p, "n_events": res.n_events,
                        "pct": res.table["pct"].max(), "verdict": res.verdict})
        except ValueError as e:
            row = {"param": p, "error": str(e)}
        rows.append(row)
    return pd.DataFrame(rows)


def cap_tier_audit(panel: Panel, signal: pd.DataFrame, **kw) -> pd.DataFrame:
    """
    市值分层审计：对 small/mid/big 三池**分别**做池内随机对照。
    任何"某层有效"的结论都必须通过该函数 —— 分层后不做层内随机对照是主要误判来源。
    """
    rows = []
    for name, mask in market_cap_tiers(panel).items():
        try:
            res = quick_audit(panel, signal, pool=mask, **kw)
            b = res.table.loc[res.table["pct"].idxmax()]
            rows.append({"tier": name, "n": res.n_events, "exc": b["exc"],
                         "t": b["t"], "boot": b["boot"], "pct": b["pct"],
                         "verdict": res.verdict})
        except ValueError as e:
            rows.append({"tier": name, "error": str(e)})
    return pd.DataFrame(rows)
