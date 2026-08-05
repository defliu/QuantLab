# coding: utf-8
"""Shared rebalance-calendar helpers for target-weight strategies.

Keeps the "when do I rebalance" logic in ONE place so every strategy doesn't
re-implement the monthly/weekly/quarterly first-trading-day rule.
"""
import pandas as pd


def is_rebalance_day(current_date, freq, calendar):
    """Return True if `current_date` is the first trading day of its
    week / month / quarter.

    freq: 'weekly' | 'monthly' | 'quarterly' | None/''  (None -> every day)
    calendar: list of trading-day strings (the run calendar).
    """
    if not freq:
        return True
    try:
        cur = pd.Timestamp(current_date)
    except Exception:
        return True
    if calendar:
        try:
            if freq == "monthly":
                month_start = cur.replace(day=1)
                month_end = month_start + pd.offsets.MonthEnd(0)
            elif freq == "quarterly":
                q_start_month = ((cur.month - 1) // 3) * 3 + 1
                month_start = cur.replace(month=q_start_month, day=1)
                month_end = month_start + pd.offsets.QuarterEnd(0)
            else:  # weekly
                month_start = cur - pd.Timedelta(days=cur.weekday())
                month_end = month_start + pd.Timedelta(days=6)
            window = [d for d in calendar
                      if pd.Timestamp(d) >= month_start and pd.Timestamp(d) <= month_end]
            if window:
                return cur.strftime("%Y-%m-%d") == str(min(window))
        except Exception:
            pass
    # fallback: weekday heuristic
    if freq == "monthly":
        return cur.day == 1 or cur.weekday() == 0
    if freq == "quarterly":
        return cur.day == 1 or cur.weekday() == 0
    return cur.weekday() == 0
