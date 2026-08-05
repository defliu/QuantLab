# coding: utf-8
"""Target-weight rebalance layer — the universal "plug any strategy" abstraction.

Goal: a strategy only needs to emit ``target_weights`` (a dict {code: weight},
weights > 0 means "hold this code at this *relative* weight). The engine then
does ALL the order bookkeeping a strategy used to duplicate by hand:

    * diff against current holdings  -> sells (full exit / partial reduce)
    * size positions (equal / vol-parity / custom magnitudes)
    * cap single-industry exposure
    * vol-targeting  (scale weights to hit an ex-ante portfolio vol)
    * leverage cap   (scale weights so sum(weights) <= target_leverage)
    * max_positions / min_position_value trimming
    * convert to the engine's native sell_decisions + buy_candidates

This keeps the FROZEN trade schema (trades.csv columns) and decision schema
untouched: the output is just ordinary sell/buy decisions the existing
execution.py consumes. Adding this path is purely additive.

Why this makes the framework general:
  - Before: every strategy reimplemented "sell held-not-in-newlist, buy
    newlist-not-held" plus its own sizing/leverage hacks.
  - After: any strategy = a ranking/weight function. Risk overlays are
    CONFIG-DRIVEN and shared by every strategy.
"""
import logging

log = logging.getLogger(__name__)


def _ann_vol(df, n):
    """Trailing annualized volatility from close prices (last `n` bars)."""
    if df is None or len(df) < 2:
        return 0.0
    close = df["close"]
    if not hasattr(close, "pct_change"):
        return 0.0
    rets = close.astype(float).pct_change().dropna()
    if len(rets) < 5:
        return 0.0
    sub = rets.iloc[-n:]
    if len(sub) < 2:
        return 0.0
    sd = float(sub.std())
    if sd <= 0 or _math_isnan(sd):
        return 0.0
    return sd * (252.0 ** 0.5)


def _math_isnan(x):
    try:
        return x != x
    except Exception:
        return False


def _normalize(d):
    s = sum((v for v in d.values() if v and v > 0), 0.0)
    if s <= 0:
        return {k: 0.0 for k in d}
    return {k: (v / s if v and v > 0 else 0.0) for k, v in d.items()}


def _apply_industry_cap(weights, industry_map, cap):
    """Cap each industry group's total weight at `cap` (proportional scale-down)."""
    if not industry_map or cap <= 0:
        return dict(weights)
    groups = {}
    for c, w in weights.items():
        if w <= 0:
            continue
        ind = industry_map.get(c, "UNKNOWN")
        groups.setdefault(ind, {})[c] = w
    out = {}
    for ind, gw in groups.items():
        s = sum(gw.values())
        if s > cap + 1e-12:
            for c, w in gw.items():
                out[c] = w * (cap / s)
        else:
            out.update(gw)
    return out


def _est_portfolio_vol(weights, market_window, vol_window):
    """Conservative ex-ante portfolio vol (diagonal only, ignores correlation).

    Using the diagonal term alone OVER-estimates variance, so the resulting
    leverage is conservative — safe for a backtest framework.
    """
    total = 0.0
    for c, w in weights.items():
        if w <= 0:
            continue
        sd = _ann_vol(market_window.get(c), vol_window)
        if sd > 0:
            total += abs(w) * sd
    return total


def target_weights_to_decision(target_weights, pf, date, config,
                               market_window, industry_map=None):
    """Convert a strategy's target_weights into a native rebalance decision.

    Args:
        target_weights: dict {code: weight}. weight > 0 -> hold/select.
                        Magnitudes matter only when position_sizing='custom'.
        pf:            Portfolio instance (current holdings + total_asset).
        date:          current_date string (signal day; fills happen T+1).
        config:        strategy_config dict (overlays read from here).
        market_window: dict {code: DataFrame} sliced to <= date (for sigma).
        industry_map:  optional dict {code: industry} for industry_cap.

    Returns:
        decision dict {sell_decisions, buy_candidates, target_positions,
                       blocked_candidates, diagnostics, logs}
    """
    cfg = config or {}
    position_sizing = str(cfg.get("position_sizing", "equal")).lower()
    target_leverage = float(cfg.get("target_leverage", 1.0))
    vol_target = float(cfg.get("vol_target", 0.0))       # 0 = off
    industry_cap = float(cfg.get("industry_cap", 0.0))   # 0 = off
    max_positions = int(cfg.get("max_positions", 0))     # 0 = no limit
    min_position_value = float(cfg.get("min_position_value", 0.0))
    vol_window = int(cfg.get("vol_window", 60))

    if target_leverage < 1.0:
        target_leverage = 1.0

    total_asset = float(pf.total_asset())
    logs = []
    diag = {"warnings": []}

    # 1) selection
    sel = {c: w for c, w in (target_weights or {}).items()
           if w is not None and w > 0}
    if not sel:
        logs.append("rebalance %s: empty target_weights -> full exit" % date)
        return _full_exit_decision(pf, date, logs, diag)

    # 2) base weights by sizing model
    if position_sizing == "vol_parity":
        raw = {}
        for c in sel:
            sd = _ann_vol(market_window.get(c), vol_window)
            raw[c] = (1.0 / sd) if sd > 1e-9 else 0.0
        if sum(raw.values()) <= 0:
            raw = {c: 1.0 for c in sel}
        base = _normalize(raw)
    elif position_sizing == "custom":
        base = _normalize({c: abs(w) for c, w in sel.items()})
    else:  # equal
        base = {c: 1.0 / len(sel) for c in sel}

    # 3) industry cap
    if industry_cap > 0:
        before = sum(base.values())
        base = _apply_industry_cap(base, industry_map, industry_cap)
        after = sum(base.values())
        if before - after > 1e-9:
            logs.append("industry_cap trimmed exposure %.2f -> %.2f"
                        % (before, after))

    # 4) max positions (keep top-N by weight, renormalize)
    if max_positions > 0 and len(base) > max_positions:
        top = sorted(base.items(), key=lambda kv: kv[1], reverse=True)[:max_positions]
        base = _normalize({c: w for c, w in top})
        logs.append("max_positions trimmed to %d" % max_positions)

    # 5) min position value filter
    if min_position_value > 0 and total_asset > 0:
        min_w = min_position_value / total_asset
        kept = {c: w for c, w in base.items() if w >= min_w}
        if kept:
            base = _normalize(kept)
            logs.append("min_position_value dropped %d tiny weights"
                        % (len(base) - len(kept)))

    # 6) vol targeting + leverage scaling
    # 财务正确语义：vol_target 先算到达波动率目标所需的毛敞口（vt_scale），
    # 杠杆在其之上相乘，且杠杆是硬上限（gross 不超过 target_leverage）。
    # 注意：不能用 min(vt_scale, target_leverage) —— 那样 vol_target 打开时
    # 杠杆会被永久压在 vt_scale 之下而静默失效（实测 1.5x+vol_target=0.10
    # 竟只给 0.45x 敞口，年化从 18.7% 跌到 8.6%）。
    scale = target_leverage
    est_vol = 0.0
    if vol_target > 0 and base:
        est_vol = _est_portfolio_vol(base, market_window, vol_window)
        if est_vol > 1e-9:
            vt_scale = vol_target / est_vol
            scale = min(vt_scale * target_leverage, target_leverage)
            logs.append("vol_target=%.2f est_vol=%.2f vt_scale=%.3f -> scale=%.3f"
                        % (vol_target, est_vol, vt_scale, scale))
    if scale != 1.0 and base:
        base = {c: w * scale for c, w in base.items()}

    gross = sum(base.values())
    diag["target_gross_leverage"] = round(gross, 4)
    diag["target_weights_count"] = len(base)
    diag["est_portfolio_vol"] = round(est_vol, 4)

    # 7) build sell/buy decisions by diffing vs current holdings
    held = {p["code"]: p for p in pf.position_list()}
    sell_decisions = []
    buy_candidates = []

    for c, w in base.items():
        target_cash = w * total_asset
        pos = held.get(c)
        if pos is None:
            buy_candidates.append({
                "code": c,
                "target_cash": target_cash,
                "reason": "target_weight_new",
                "layer": "confirm",
                "rank": 0,
            })
        else:
            cur_val = float(pos["volume"]) * float(pos["last_price"])
            if target_cash > cur_val + 1e-6:
                buy_candidates.append({
                    "code": c,
                    "target_cash": target_cash - cur_val,
                    "reason": "rebalance_increase",
                    "layer": "confirm",
                    "rank": 0,
                })
            elif target_cash < cur_val - 1e-6:
                sell_decisions.append({
                    "code": c,
                    "target_cash": cur_val - target_cash,
                    "reason": "rebalance_reduce",
                    "layer": "confirm",
                })

    # full exits for held codes not in target
    for c, pos in held.items():
        if c not in base:
            sell_decisions.append({
                "code": c,
                "reason": "target_exit",
                "layer": "confirm",
            })

    diag["candidate_total"] = len(sel)
    diag["candidate_passed"] = len(base)
    diag["buy_count"] = len(buy_candidates)
    diag["sell_count"] = len(sell_decisions)

    return {
        "sell_decisions": sell_decisions,
        "buy_candidates": buy_candidates,
        "target_positions": [{"code": c, "weight": round(w, 6)}
                              for c, w in sorted(base.items(),
                                                 key=lambda kv: kv[1],
                                                 reverse=True)],
        "blocked_candidates": [],
        "diagnostics": diag,
        "logs": logs,
    }


def _full_exit_decision(pf, date, logs, diag):
    sell_decisions = [{"code": c, "reason": "target_exit", "layer": "confirm"}
                      for c in pf.positions.keys()]
    diag["candidate_total"] = 0
    diag["candidate_passed"] = 0
    diag["buy_count"] = 0
    diag["sell_count"] = len(sell_decisions)
    diag["target_gross_leverage"] = 0.0
    diag["target_weights_count"] = 0
    diag["est_portfolio_vol"] = 0.0
    return {
        "sell_decisions": sell_decisions,
        "buy_candidates": [],
        "target_positions": [],
        "blocked_candidates": [],
        "diagnostics": diag,
        "logs": logs,
    }
