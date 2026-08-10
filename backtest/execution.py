# coding: utf-8
"""Execution / matching layer for QuantLab backtest engine.

Contract:
  - trades.csv columns: run_id, date, code, side, volume, price,
    amount, slippage_amt, commission, tax, reason, layer, model
  - summary.execution echoes price/slippage/commission_rate/tax_rate
  - target_volume from strategy is always 0 (OQ-F); engine converts
    target_cash -> integer volumes here.

Model: next_open.
  - buy fills at T+1 open * (1 + slippage)
  - sell fills at T+1 open * (1 - slippage)
  - if T+1 has no bar (suspended) -> unfilled (logged, counted)
  - if T+1 open at >= dynamic limit-up -> buy is rejected;
    if T+1 open at <= dynamic limit-down -> sell is rejected
  - A-share lot = 100 shares; volume = floor(cash / price / 100) * 100
  - optional liquidity cap (exec_cfg.max_adv_pct, default 0=off):
    a buy whose target_cash exceeds (prev-day amount * max_adv_pct) is
    rejected as 'capacity_exceeded' (see fill_buy).

Pure-ish: no IO, no randomness, no global mutable state. Returns trade dicts
matching the trades.csv column schema.
"""

import numpy as np


_LOT_SIZE = 100


def _price_limit(code, bar=None):
    """涨跌停幅度（小数）：双创 0.20 / 北交 0.30 / 主板 0.10 / ST 0.05。

    ST 由 bar 的 is_st 字段判定（reader 需输出 is_st 列）；缺失时按普通板。
    """
    c = code.split(".")[0] if "." in code else code
    is_st = False
    if bar is not None:
        try:
            is_st = bool(bar.get("is_st", False))
        except Exception:
            is_st = False
    if is_st:
        return 0.05
    if c.startswith("300") or c.startswith("688"):
        return 0.20
    if c.startswith("8") and len(c) == 6:
        return 0.30
    return 0.10


def _lot_floor(volume):
    """Round volume DOWN to nearest 100-share lot."""
    if volume < _LOT_SIZE:
        return 0
    return int(volume // _LOT_SIZE) * _LOT_SIZE


def _bar_lookup(market_window, code, fill_date):
    """Return (bar, prev_bar) for `code` on `fill_date`.

    - bar:      the row (pd.Series) for fill_date, or None if the bar is
                missing (suspended / out-of-range).
    - prev_bar: the previous row (prior trading day), or None if bar is
                missing / no prior day. Used for open-pct (limit detection)
                and the liquidity-cap proxy (prev-day amount).

    market_window[code] is a DataFrame indexed 0..N-1 with a 'date' column
    that is sorted ascending (see data readers). We binary-search on the
    date strings so each lookup is O(log N) instead of a full-column scan.
    """
    df = (market_window or {}).get(code)
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None, None
    dates = df["date"].astype(str).values
    fd = str(fill_date)
    idx = int(np.searchsorted(dates, fd, side="left"))
    if idx >= len(dates) or dates[idx] != fd:
        return None, None
    bar = df.iloc[idx]
    prev = df.iloc[idx - 1] if idx > 0 else None
    return bar, prev


def _open_pct(bar, prev_bar):
    """Open percent change vs previous close for fill_date.

    Used to detect limit-up / limit-down at the OPEN of the fill day (which
    prevents buy / sell fills under the next_open model). Returns 0.0 if the
    previous close is unavailable.
    """
    if prev_bar is None:
        return 0.0
    try:
        prev_close = float(prev_bar["close"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if prev_close <= 0:
        return 0.0
    try:
        open_price = float(bar["open"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    return (open_price - prev_close) / prev_close * 100.0


def fill_sell(decision, position, market_window, fill_date, exec_cfg, run_id):
    """Match a sell decision against the next_open bar.

    Returns (trade_dict_or_None, unfilled_reason_or_None).
    """
    code = decision["code"]
    bar, prev = _bar_lookup(market_window, code, fill_date)
    if bar is None:
        return (None, "suspended")
    avail = int(position.get("available_volume", 0))
    if avail <= 0:
        return (None, "no_available_volume")
    open_price = float(bar["open"])
    if open_price <= 0:
        return (None, "invalid_price")
    open_pct = _open_pct(bar, prev)
    limit = _price_limit(code, bar)
    if open_pct <= -limit * 100 + 0.05:
        return (None, "limit_down_at_open")

    slippage = float(exec_cfg.get("slippage", 0.001))
    commission_rate = float(exec_cfg.get("commission_rate", 0.00025))
    tax_rate = float(exec_cfg.get("tax_rate", 0.0001))

    price = round(open_price * (1.0 - slippage), 6)
    # 部分减仓：rebalance 层可传 target_cash 指定卖出金额（整数手向下取整）。
    # 未传 / 为 0 时沿用原行为：清仓（卖出全部可用）。
    target_cash = float(decision.get("target_cash", 0.0) or 0.0)
    if target_cash > 0:
        raw_vol = target_cash / price
        volume = _lot_floor(raw_vol)
        if volume <= 0:
            return (None, "below_min_lot")
        if volume > avail:
            volume = avail
    else:
        volume = avail
    amount = round(price * volume, 6)
    slippage_amt = round(open_price * slippage * volume, 6)
    commission = round(amount * commission_rate, 6)
    tax = round(amount * tax_rate, 6)

    trade = {
        "run_id":       run_id,
        "date":         str(fill_date),
        "code":         code,
        "side":         "sell",
        "volume":       volume,
        "price":        price,
        "amount":       amount,
        "slippage_amt": slippage_amt,
        "commission":   commission,
        "tax":          tax,
        "reason":       decision["reason"],
        "layer":        decision.get("layer", ""),
        "model":        exec_cfg.get("price", "next_open"),
    }
    return (trade, None)


def fill_buy(candidate, market_window, fill_date, exec_cfg, run_id):
    """Match a buy candidate against the next_open bar.

    target_cash from candidate is converted to lot-floored volume here.
    Returns (trade_dict_or_None, unfilled_reason_or_None).
    """
    code = candidate["code"]
    bar, prev = _bar_lookup(market_window, code, fill_date)
    if bar is None:
        return (None, "suspended")
    open_pct = _open_pct(bar, prev)
    limit = _price_limit(code, bar)
    if open_pct >= limit * 100 - 0.05:
        return (None, "limit_up_at_open")
    open_price = float(bar["open"])
    if open_price <= 0:
        return (None, "invalid_price")

    slippage = float(exec_cfg.get("slippage", 0.001))
    commission_rate = float(exec_cfg.get("commission_rate", 0.00025))

    price = round(open_price * (1.0 + slippage), 6)
    target_cash = float(candidate.get("target_cash", 0.0))
    if target_cash <= 0 or price <= 0:
        return (None, "no_target_cash")

    # 流动性容量上限（可选）：以前一交易日成交额为代理，避免使用当日
    # 容量造成前视。max_adv_pct=0（默认）关闭本约束，行为与旧版一致。
    max_adv_pct = float(exec_cfg.get("max_adv_pct", 0.0) or 0.0)
    if max_adv_pct > 0:
        try:
            prev_amount = float(prev["amount"]) if prev is not None else 0.0
        except (KeyError, TypeError, ValueError):
            prev_amount = 0.0
        if prev_amount > 0 and target_cash > prev_amount * max_adv_pct:
            return (None, "capacity_exceeded")

    raw_vol = target_cash / price
    volume = _lot_floor(raw_vol)
    if volume <= 0:
        return (None, "below_min_lot")
    amount = round(price * volume, 6)
    slippage_amt = round(open_price * slippage * volume, 6)
    commission = round(amount * commission_rate, 6)

    trade = {
        "run_id":       run_id,
        "date":         str(fill_date),
        "code":         code,
        "side":         "buy",
        "volume":       volume,
        "price":        price,
        "amount":       amount,
        "slippage_amt": slippage_amt,
        "commission":   commission,
        "tax":          0.0,
        "reason":       candidate.get("reason", "top_candidate"),
        "layer":        "",
        "model":        exec_cfg.get("price", "next_open"),
    }
    return (trade, None)
