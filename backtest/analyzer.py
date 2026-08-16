# coding: utf-8
"""Performance metrics (analyzer) for QuantLab backtest engine.

Frozen contract: equity_curve rows + trades list. Pure numpy on float arrays;
no scipy / sklearn dependency. benchmark_available=false -> excess_return /
information_ratio / tracking_error return None.
"""
import math


def _annualize_factor(trading_days):
    """Standard 252-day annualization base; safe for short samples."""
    if trading_days <= 0:
        return 1.0
    return 252.0 / float(trading_days)


def _max_drawdown(equity):
    """min over t of (equity[t] - peak[t]) / peak[t]; returns negative or 0."""
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    return mdd


def _sharpe(returns, trading_days):
    """rf=0; mean(daily)/std(daily) * sqrt(252)."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(252.0)


def _pair_trades(trades):
    """Pair buy -> sell per code in chronological order.

    Returns (pairs, open_buys):
      pairs:     list of (buy_trade, sell_trade) tuples for closed positions.
      open_buys: list of buy_trades still held at end of run (no matching sell).

    Win-rate / avg-holding derived from `pairs` alone only cover closed
    positions; callers that also pass open_positions can fold open buys in
    (see compute_metrics) so the metrics reflect all deployed capital.
    """
    buys_by_code = {}
    pairs = []
    for t in trades:
        code = t["code"]
        if t["side"] == "buy":
            buys_by_code.setdefault(code, []).append(t)
        elif t["side"] == "sell":
            queue = buys_by_code.get(code, [])
            if queue:
                pairs.append((queue.pop(0), t))
    open_buys = []
    for queue in buys_by_code.values():
        open_buys.extend(queue)
    return pairs, open_buys


def _trading_day_diff(date_a, date_b, trading_calendar):
    """Count trading days between two YYYY-MM-DD strings (b - a, inclusive of b)."""
    cal = list(trading_calendar or [])
    a = str(date_a)
    b = str(date_b)
    try:
        ia = cal.index(a)
        ib = cal.index(b)
    except ValueError:
        # 日期不在日历中（极端边界，如 buy 日或期末日缺失）→ 无法精确计算，
        # 按"持有整个样本期"作上限兜底，避免静默报 0 天。
        return max(0, len(cal))
    return ib - ia


def _benchmark_metrics(daily_returns, benchmark_returns, total_return,
                       benchmark_total_return, n_days):
    """Compute (excess_return, information_ratio, tracking_error)."""
    diffs = [float(s) - float(b) for s, b in zip(daily_returns, benchmark_returns)]
    excess = float(total_return) - float(benchmark_total_return)
    if len(diffs) < 2:
        return excess, None, None
    mean = sum(diffs) / len(diffs)
    var = sum((x - mean) ** 2 for x in diffs) / (len(diffs) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    tracking = std * math.sqrt(252.0)
    if std > 0:
        info_ratio = mean / std * math.sqrt(252.0)
    else:
        info_ratio = None
    return excess, info_ratio, tracking


def compute_metrics(equity_rows, trades, trading_calendar,
                    initial_cash, benchmark_available=False,
                    benchmark_returns=None, benchmark_total_return=None,
                    open_positions=None):
    """Compute performance dict.

    Args:
        equity_rows: list of dicts from Portfolio.equity_row in order.
        trades: list of trade dicts from execution.fill_*.
        trading_calendar: list of YYYY-MM-DD trading-day strings.
        initial_cash: float, starting cash.
        benchmark_available: bool. False -> excess/info/tracking are None.
        benchmark_returns: list of float daily returns aligned with
            equity_rows[1:] (same length as the strategy daily_returns slice).
        benchmark_total_return: float, cumulative return of the benchmark.
        open_positions: optional list of end-of-run position dicts
            ({code, cost_price, last_price, volume, ...}). When provided,
            buys still held at the end are valued at their last price and
            folded into win-rate / avg-holding; otherwise (default None)
            win-rate only covers closed positions (legacy behaviour).
    """
    n_days = len(trading_calendar) if trading_calendar else len(equity_rows)
    if n_days <= 0:
        n_days = 1

    if equity_rows:
        end_total = float(equity_rows[-1]["total_asset"])
    else:
        end_total = float(initial_cash)
    total_return = (end_total / float(initial_cash)) - 1.0 if initial_cash > 0 else 0.0
    annual_return = total_return * _annualize_factor(n_days)
    # CAGR（复利年化）：保留 annual_return 线性口径不动（下游兼容），新增 cagr
    # 以复利为准。total_return <= -1 或样本过短时退化为线性口径兜底。
    if n_days > 0 and (1.0 + total_return) > 0:
        cagr = (1.0 + total_return) ** (252.0 / n_days) - 1.0
    else:
        cagr = annual_return

    equity_series = [float(r["total_asset"]) for r in equity_rows]
    daily_returns = [float(r["daily_return"]) for r in equity_rows[1:]]  # skip day 0

    mdd = _max_drawdown(equity_series)
    sharpe = _sharpe(daily_returns, n_days)
    if mdd != 0:
        calmar = annual_return / abs(mdd)
        cagr_calmar = cagr / abs(mdd)
    else:
        calmar = None
        cagr_calmar = None

    pairs, open_buys = _pair_trades(trades)
    n_buy = sum(1 for t in trades if t["side"] == "buy")
    n_sell = sum(1 for t in trades if t["side"] == "sell")

    # ---- win-rate / avg-holding ----
    # Fold end-of-run open buys into the metrics (valued at last price) when
    # open_positions is provided, so every deployed dollar is counted.
    open_by_code = {}
    if open_positions:
        for p in open_positions:
            open_by_code.setdefault(p["code"], []).append(p)
    end_date = trading_calendar[-1] if trading_calendar else (
        equity_rows[-1].get("date") if equity_rows else None)

    wins = 0
    hold_sum = 0
    count = 0
    for buy, sell in pairs:
        buy_amt = float(buy["amount"]) + float(buy["commission"])
        sell_amt = float(sell["amount"]) - float(sell["commission"]) - float(sell["tax"])
        if sell_amt > buy_amt:
            wins += 1
        hold_sum += _trading_day_diff(buy["date"], sell["date"], trading_calendar)
        count += 1
    # unmatched buys still open at end of run
    for buy in open_buys:
        code = buy["code"]
        ops = open_by_code.get(code)
        # find the open position that this buy corresponds to (last remaining)
        if not ops:
            continue
        p = ops[-1]
        buy_amt = float(buy["amount"]) + float(buy["commission"])
        # notional value at end-of-run last price
        end_amt = float(p["last_price"]) * float(buy["volume"])
        if end_amt > buy_amt:
            wins += 1
        if end_date is not None:
            hold_sum += _trading_day_diff(buy["date"], end_date, trading_calendar)
        count += 1

    if count > 0:
        win_rate = wins / float(count)
        avg_holding_days = hold_sum / float(count)
    else:
        win_rate = 0.0
        avg_holding_days = 0.0
    n_open = len(open_buys)

    perf = {
        "total_return":     round(total_return, 6),
        "annual_return":    round(annual_return, 6),
        "cagr":             cagr,
        "max_drawdown":     round(mdd, 6),
        "sharpe":           round(sharpe, 6),
        "calmar":           None if calmar is None else round(calmar, 6),
        "cagr_calmar":      None if cagr_calmar is None else cagr_calmar,
        "win_rate":         round(win_rate, 6),
        "n_trades":         len(trades),
        "n_buy":            n_buy,
        "n_sell":           n_sell,
        "n_open":           n_open,
        "avg_holding_days": round(avg_holding_days, 6),
        "excess_return":     None,
        "information_ratio": None,
        "tracking_error":    None,
    }
    if benchmark_available and benchmark_returns is not None \
            and benchmark_total_return is not None \
            and len(benchmark_returns) == len(daily_returns):
        excess, ir, te = _benchmark_metrics(
            daily_returns, benchmark_returns,
            total_return, benchmark_total_return, n_days)
        perf["excess_return"] = round(excess, 6)
        perf["information_ratio"] = None if ir is None else round(ir, 6)
        perf["tracking_error"] = None if te is None else round(te, 6)
    return perf
