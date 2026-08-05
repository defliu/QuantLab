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

Pure-ish: no IO, no randomness, no global mutable state. Returns trade dicts
matching the trades.csv column schema.
"""


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


def _next_open_bar(market_window, code, fill_date):
    """Return the row dict (open, high, low, close, ...) for code on fill_date.

    market_window[code] is a DataFrame indexed 0..N-1 with a 'date' column.
    Returns None if the bar is missing (suspended / out-of-range).
    """
    df = (market_window or {}).get(code)
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    fd = str(fill_date)
    matches = df[df["date"].astype(str) == fd]
    if len(matches) == 0:
        return None
    return matches.iloc[0]


def _open_pct(market_window, code, fill_date):
    """Open percent change vs previous close for fill_date.

    Used to detect limit-up at the OPEN of the fill day (which would prevent
    buy fills under next_open model). Returns 0.0 if data missing.
    """
    df = (market_window or {}).get(code)
    if df is None or len(df) < 2 or "date" not in df.columns:
        return 0.0
    fd = str(fill_date)
    rows = df.reset_index(drop=True)
    idx_match = rows.index[rows["date"].astype(str) == fd].tolist()
    if not idx_match:
        return 0.0
    i = idx_match[0]
    if i == 0:
        return 0.0
    prev_close = float(rows["close"].iloc[i - 1])
    if prev_close <= 0:
        return 0.0
    open_price = float(rows["open"].iloc[i])
    return (open_price - prev_close) / prev_close * 100.0


def fill_sell(decision, position, market_window, fill_date, exec_cfg, run_id):
    """Match a sell decision against the next_open bar.

    Returns (trade_dict_or_None, unfilled_reason_or_None).
    """
    code = decision["code"]
    bar = _next_open_bar(market_window, code, fill_date)
    if bar is None:
        return (None, "suspended")
    avail = int(position.get("available_volume", 0))
    if avail <= 0:
        return (None, "no_available_volume")
    open_price = float(bar["open"])
    if open_price <= 0:
        return (None, "invalid_price")
    open_pct = _open_pct(market_window, code, fill_date)
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
    bar = _next_open_bar(market_window, code, fill_date)
    if bar is None:
        return (None, "suspended")
    open_pct = _open_pct(market_window, code, fill_date)
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
