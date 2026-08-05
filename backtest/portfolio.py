# coding: utf-8
"""Portfolio bookkeeping for QuantLab backtest engine.

Tracks cash, positions, hold_days. Applies executed trades returned from
execution.py and produces daily snapshots conforming to:
  - equity_curve.csv (date, total_asset, cash, market_value, daily_return,
    benchmark_close, benchmark_return)
  - positions.csv (date, code, volume, available_volume, cost_price,
    last_price, unrealized_pnl, holding_days)

Constraints:
  - Pure data structures + helpers; no IO.
  - Buy lot rounding is done in execution.py; portfolio receives integer volume.
"""


def _bar_close(market_window, code, date):
    df = (market_window or {}).get(code)
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    rows = df[df["date"].astype(str) == str(date)]
    if len(rows) == 0:
        return None
    return float(rows["close"].iloc[0])


def _last_known_close(market_window, code, date):
    """Fallback to last available close <= date when the day is suspended."""
    df = (market_window or {}).get(code)
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    sub = df[df["date"].astype(str) <= str(date)]
    if len(sub) == 0:
        return None
    return float(sub["close"].iloc[-1])


class Portfolio(object):
    def __init__(self, initial_cash):
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        # positions keyed by code -> dict
        self.positions = {}
        # last total_asset for daily_return base
        self._last_total_asset = float(initial_cash)

    # ------------------------------------------------------------------
    # Snapshot / read-only helpers
    # ------------------------------------------------------------------
    def position_list(self):
        """Return positions formatted for evaluate_day input."""
        out = []
        for code, p in self.positions.items():
            out.append({
                "code":             code,
                "volume":           int(p["volume"]),
                "available_volume": int(p["available_volume"]),
                "cost_price":       float(p["cost_price"]),
                "entry_date":       p["entry_date"],
                "holding_days":     int(p["holding_days"]),
                "last_price":       float(p["last_price"]),
                "unrealized_pnl":   float(p["unrealized_pnl"]),
            })
        return out

    def market_value(self):
        return sum(float(p["volume"]) * float(p["last_price"])
                   for p in self.positions.values())

    def total_asset(self):
        return float(self.cash) + self.market_value()

    # ------------------------------------------------------------------
    # End-of-day mark + lifecycle
    # ------------------------------------------------------------------
    def mark_to_market(self, market_window, date):
        """Update last_price and unrealized_pnl using close on `date`.

        For suspended codes (no bar today) we keep the last known close.
        """
        for code, p in self.positions.items():
            close = _bar_close(market_window, code, date)
            if close is None:
                close = _last_known_close(market_window, code, date)
            if close is not None:
                p["last_price"] = float(close)
                p["unrealized_pnl"] = round(
                    (p["last_price"] - p["cost_price"]) * p["volume"], 6)

    def advance_holding_days(self):
        """Increment holding_days and unfreeze T+1 available_volume.

        Call ONCE at start-of-day before fills, so today's new buys land with
        holding_days=1 and available_volume=0 after apply_trade().
        """
        for p in self.positions.values():
            p["holding_days"] = int(p["holding_days"]) + 1
            p["available_volume"] = int(p["volume"])  # unfreeze

    # ------------------------------------------------------------------
    # Trade application (called in chronological fill order)
    # ------------------------------------------------------------------
    def apply_trade(self, trade):
        """Apply an executed trade dict (from execution.py) to portfolio.

        Updates cash, positions, available_volume according to T+1 rule:
        new buys are NOT immediately available for sale (available_volume=0
        on the buy day; advance_holding_days() unfreezes them next day).
        """
        side = trade["side"]
        code = trade["code"]
        volume = int(trade["volume"])
        amount = float(trade["amount"])
        commission = float(trade["commission"])
        tax = float(trade["tax"])

        if side == "buy":
            self.cash -= (amount + commission)
            if code in self.positions:
                p = self.positions[code]
                old_v = p["volume"]
                old_cost = p["cost_price"]
                new_v = old_v + volume
                new_cost = (old_cost * old_v + amount + commission) / new_v
                p["volume"] = new_v
                p["cost_price"] = round(new_cost, 6)
                # New buy fraction is locked under T+1; available remains old_v.
                p["available_volume"] = int(old_v)
                p["last_price"] = float(trade["price"])
            else:
                self.positions[code] = {
                    "volume":           volume,
                    "available_volume": 0,                         # T+1
                    "cost_price":       round((amount + commission) / volume, 6),
                    "entry_date":       trade["date"],
                    "holding_days":     1,
                    "last_price":       float(trade["price"]),
                    "unrealized_pnl":   0.0,
                }
        elif side == "sell":
            self.cash += (amount - commission - tax)
            if code in self.positions:
                p = self.positions[code]
                p["volume"] = int(p["volume"]) - volume
                p["available_volume"] = max(0, int(p["available_volume"]) - volume)
                p["last_price"] = float(trade["price"])
                if p["volume"] <= 0:
                    del self.positions[code]
        else:
            raise ValueError("unknown side: " + str(side))

    # ------------------------------------------------------------------
    # Equity curve / positions.csv row builders
    # ------------------------------------------------------------------
    def equity_row(self, run_id, date):
        ta = self.total_asset()
        prev = self._last_total_asset if self._last_total_asset > 0 else ta
        if prev > 0:
            daily_ret = (ta - prev) / prev
        else:
            daily_ret = 0.0
        self._last_total_asset = ta
        return {
            "run_id":           run_id,
            "date":             str(date),
            "total_asset":      round(ta, 6),
            "cash":             round(self.cash, 6),
            "market_value":     round(self.market_value(), 6),
            "daily_return":     round(daily_ret, 8),
            "benchmark_close":  "",
            "benchmark_return": "",
        }

    def positions_rows(self, run_id, date):
        rows = []
        for code, p in self.positions.items():
            rows.append({
                "run_id":           run_id,
                "date":             str(date),
                "code":             code,
                "volume":           int(p["volume"]),
                "available_volume": int(p["available_volume"]),
                "cost_price":       round(float(p["cost_price"]), 6),
                "last_price":       round(float(p["last_price"]), 6),
                "unrealized_pnl":   round(float(p["unrealized_pnl"]), 6),
                "holding_days":     int(p["holding_days"]),
            })
        return rows
