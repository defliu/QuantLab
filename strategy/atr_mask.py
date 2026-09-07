# coding: utf-8
"""ATR 低波动策略 —— 涨跌停掩码前置版（T-20260829-001 对比实验）。

相对 strategy/atr_lowvol.py 的唯一差异：在算子计算前先构造涨跌停/停牌掩码，
  * 计算 ATR% 时剔除涨停/跌停/停牌的 frozen bar（用清洗后的真实交易日序列算 TR）
  * 若调仓日该股票末根 bar 处于涨跌停或停牌，直接剔除（不可成交）
其余选股/仓位逻辑与原版完全一致，保证对比公平。

注册名：atr_lowvol_mask
"""
from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day
from factors.roe import get_roe_asof

import numpy as np
import pandas as pd

ALLOWED_TRADING_MODELS = ["next_open"]


def _real_close_of(cl, af):
    """复权收盘价 -> 真实收盘价（cl / adj_factor）；缺 adj_factor 时回退复权价。"""
    try:
        cl = float(cl)
        af = float(af) if af is not None else 0.0
    except Exception:
        return None
    if af and af > 0:
        return cl / af
    return cl


def _bar_masked(row):
    """该 bar 是否价格不可成交（涨停/跌停/停牌/无成交）。

    注意：suspend_type 仅 'N'(正常) 与空 视为可交易；'R'/'S'/'R&S' 等是
    停牌/风险/暂停，必须 mask。涨跌停须用「真实收盘价 = 复权价/adj_factor」
    与 up_limit/down_limit 比较（复权价是合成数，直接比永不相等）。
    """
    # 无成交 / 停牌（成交量为 0 或 NaN）
    try:
        v = row.get("vol")
        if v is None or (isinstance(v, float) and np.isnan(v)) or float(v) <= 0:
            return True
    except Exception:
        pass
    # 停牌/风险/暂停：suspend_type 仅 N(正常) 或 空 视为可交易
    st = row.get("suspend_type")
    if st is not None and not (isinstance(st, float) and np.isnan(st)) and st not in ("", "N"):
        return True
    # 涨跌停（用真实价比较）
    ul = row.get("up_limit")
    dl = row.get("down_limit")
    cl = row.get("close")
    af = row.get("adj_factor")
    if ul is not None and dl is not None and cl is not None:
        rc = _real_close_of(cl, af)
        if rc is None:
            return False
        try:
            ul = float(ul); dl = float(dl)
        except Exception:
            return False
        if ul > 0 and abs(rc - ul) < 1e-6:
            return True
        if dl > 0 and abs(rc - dl) < 1e-6:
            return True
    return False


def _masked_atr_pct(df, n):
    """剔除涨停/跌停/停牌/无成交 bar 后，用真实交易日序列算 ATR%（清洗法）。

    对应报告「mask-aware」：算子先过 mask 再计算，避免涨停虚假高价参与
    波动率估计。向量化实现，避免 iterrows 性能陷阱。
    """
    if df is None or len(df) < n + 2:
        return -1.0
    close = df["close"].astype(float).values
    high = df["high"].astype(float).values
    low = df["low"].astype(float).values
    nn = len(close)
    bad = np.zeros(nn, dtype=bool)
    bad |= ~np.isfinite(close) | (close <= 0)
    # 无成交 / NaN
    if "vol" in df.columns:
        vol = df["vol"].astype(float).values
        bad |= ~np.isfinite(vol) | (vol <= 0)
    # 停牌/风险/暂停（suspend_type 非 N/空）
    if "suspend_type" in df.columns:
        st_s = df["suspend_type"].fillna("").astype(str).values
        bad |= (st_s != "") & (st_s != "N")
    # 涨跌停（真实价）
    af = df["adj_factor"].astype(float).values if "adj_factor" in df.columns else None
    rc = np.where((af is not None) & (af > 0) & np.isfinite(af), close / af, close) if af is not None else close
    if "up_limit" in df.columns:
        ul = df["up_limit"].astype(float).values
        bad |= (ul > 0) & (np.abs(rc - ul) < 1e-6)
    if "down_limit" in df.columns:
        dl = df["down_limit"].astype(float).values
        bad |= (dl > 0) & (np.abs(rc - dl) < 1e-6)
    idx = np.where(~bad)[0]
    if len(idx) < n + 1:
        return -1.0
    c = close[idx]; h = high[idx]; l = low[idx]
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(np.maximum(h - l, np.abs(h - pc)), np.abs(l - pc))
    if c[-1] <= 0:
        return -1.0
    return float(tr[-n:].mean() / c[-1])


@register_strategy("atr_lowvol_mask")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "monthly")
    n_hold = int(cfg.get("n_hold", 100))
    atr_win = int(cfg.get("atr_win", 14))
    atr_pct_max = float(cfg.get("atr_pct_max", 0.06))
    turnover_min = float(cfg.get("turnover_min", 1.0))
    turnover_max = float(cfg.get("turnover_max", 8.0))
    quality_gate = int(cfg.get("quality_gate", 1))
    momentum_gate = int(cfg.get("momentum_gate", 1))
    stop_loss = cfg.get("stop_loss", None)
    if stop_loss is not None:
        stop_loss = float(stop_loss)
    min_history = int(cfg.get("min_history", 252))
    max_price = float(cfg.get("max_price", 0) or 0)
    market_gate = int(cfg.get("market_gate", 0) or 0)
    ma_window = int(cfg.get("ma_window", 200))
    gate_mode = str(cfg.get("gate_mode", "exit")).lower()
    ranking = str(cfg.get("ranking", "atr")).lower()
    max_exclude_pct = float(cfg.get("max_exclude_pct", 0) or 0)
    rebalance_buffer = int(cfg.get("rebalance_buffer", 0) or 0)

    # 非调仓日：保持持仓（含止损）
    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        if stop_loss is None or stop_loss >= 0:
            return {
                "sell_decisions": [], "buy_candidates": [],
                "target_positions": [], "blocked_candidates": [],
                "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                                "candidate_passed": 0},
                "logs": ["%s hold" % current_date],
            }
        stopped = []
        keep = {}
        for p in positions:
            cost = float(p.get("cost_price", 0)) or 0.0
            last = float(p.get("last_price", 0)) or 0.0
            pnl = (last - cost) / cost if cost > 0 else 0.0
            if pnl <= stop_loss:
                stopped.append(p["code"])
            else:
                keep[p["code"]] = 1.0
        if not stopped:
            return {
                "sell_decisions": [], "buy_candidates": [],
                "target_positions": [], "blocked_candidates": [],
                "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                                "candidate_passed": 0},
                "logs": ["%s hold" % current_date],
            }
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_weights": keep,
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["stop_loss_exit:%d" % len(stopped)],
                            "candidate_total": 0, "candidate_passed": len(keep)},
            "logs": ["%s stop_loss exit %s" % (current_date, stopped)],
        }

    valid = [c for c in universe
             if c in market_window and len(market_window[c]) >= min_history]
    if not valid:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_valid_universe"],
                            "candidate_total": 0, "candidate_passed": 0},
            "logs": ["%s no valid universe" % current_date],
        }

    eligible = []
    price_filtered = 0
    for c in valid:
        df = market_window[c]
        last = df.iloc[-1]
        if _bar_masked(last):
            continue  # 末根 bar 不可成交 -> 剔除
        if max_price > 0:
            af = last.get("adj_factor")
            if af is not None and float(af) > 0:
                real_close = float(last.get("close", 0)) / float(af)
                if real_close >= max_price:
                    price_filtered += 1
                    continue
        to = last.get("turnover_rate")
        if to is None or not (turnover_min <= float(to) <= turnover_max):
            continue
        if bool(last.get("is_st", False)):
            continue
        ap = _masked_atr_pct(df, atr_win)
        if ap <= 0 or ap > atr_pct_max:
            continue
        if quality_gate:
            roe = get_roe_asof(c, current_date)
            if roe is None or roe <= 0:
                continue
        close = df["close"].astype(float).values
        ret_12_1 = 0.0
        if len(close) >= 252:
            ret_12_1 = (close[-21] / close[-252] - 1.0) if close[-252] > 0 else 0.0
        if momentum_gate and ret_12_1 <= 0:
            continue
        pbv = last.get("pb")
        bp = (1.0 / float(pbv)) if (pbv is not None and float(pbv) > 0) else 0.0
        eligible.append([c, ap, ret_12_1, bp])

    if max_exclude_pct > 0 and len(eligible) > n_hold:
        for r in eligible:
            df = market_window[r[0]]
            rr = df["close"].astype(float).pct_change().dropna()
            r.append(float(rr.tail(20).max()) if len(rr) > 0 else 0.0)
        maxs = sorted(r[4] for r in eligible)
        thr = maxs[max(0, int(len(maxs) * (1.0 - max_exclude_pct)) - 1)]
        eligible = [r for r in eligible if r[4] <= thr]

    if ranking == "momentum":
        eligible.sort(key=lambda r: r[2], reverse=True)
    elif ranking == "momentum_value":
        moms = np.array([r[2] for r in eligible], dtype=float)
        bps = np.array([r[3] for r in eligible], dtype=float)
        z_m = (moms - moms.mean()) / (moms.std() + 1e-12)
        z_b = (bps - bps.mean()) / (bps.std() + 1e-12)
        for i, r in enumerate(eligible):
            r.append(z_m[i] + z_b[i])
        eligible.sort(key=lambda r: r[-1], reverse=True)
    else:
        eligible.sort(key=lambda r: r[1])

    pool = eligible[:n_hold + rebalance_buffer]
    if rebalance_buffer > 0 and pool:
        held = {p["code"] for p in positions}
        selected = [r[0] for r in pool if r[0] in held]
        for r in pool:
            if len(selected) >= n_hold:
                break
            if r[0] not in selected:
                selected.append(r[0])
    else:
        selected = [r[0] for r in pool[:n_hold]]

    if not selected:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_selection"],
                            "candidate_total": len(valid), "candidate_passed": 0},
            "logs": ["%s no selection from %d" % (current_date, len(valid))],
        }

    target_weights = {c: 1.0 for c in selected}
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": target_weights,
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(valid),
            "candidate_passed": len(selected),
            "strategy_specific": {
                "atr_lowvol_mask": {"price_filtered": {"count": price_filtered}},
            },
        },
        "logs": ["%s rebalance(mask): %d selected from %d"
                 % (current_date, len(selected), len(valid))],
    }
