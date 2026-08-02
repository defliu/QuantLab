# coding: utf-8
"""Astock finance reader — PIT (Point-in-Time) safe fundamentals query.

Data sources:
  - E:/astock/finance/fina_indicator.parquet  (quarterly fundamentals)
  - E:/astock/daily/stock_daily.parquet       (daily PE snapshots)

PIT rule:
  A record is "visible" on date T if its announcement date (ann_date) <= T.
  We always use the latest end_date among visible records for a given code.
"""
import logging
import os

import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_FINANCE_DIR = "E:/astock/finance"
DEFAULT_DAILY_PATH = "E:/astock/daily/stock_daily.parquet"

DEFAULT_FIELDS = ("eps", "roe", "gross_margin", "netprofit_margin",
                  "bps", "q_profit_yoy")


class AstockFinanceReader(object):
    """Read-only Astock finance reader with PIT anti-lookahead query.

    Usage:
        reader = AstockFinanceReader()
        fund = reader.get_fundamentals_pit("600000.SH", "2026-06-18")
        pe = reader.get_daily_pe("600000.SH", "2026-06-18")
        scoring = reader.get_fundamentals_for_scoring(["600000.SH"], "2026-06-18")
    """

    def __init__(self, finance_dir=None, daily_path=None):
        self._finance_dir = finance_dir or DEFAULT_FINANCE_DIR
        self._daily_path = daily_path or DEFAULT_DAILY_PATH
        self._fina_df = None
        self._daily_df = None

    def _load_fina(self):
        if self._fina_df is None:
            p = os.path.join(self._finance_dir, "fina_indicator.parquet")
            if not os.path.isfile(p):
                raise FileNotFoundError("fina_indicator.parquet not found: " + p)
            self._fina_df = pd.read_parquet(p)
            log.debug("Loaded fina_indicator: %d rows", len(self._fina_df))
        return self._fina_df

    def _load_daily(self):
        if self._daily_df is None:
            if not os.path.isfile(self._daily_path):
                raise FileNotFoundError(
                    "stock_daily.parquet not found: " + self._daily_path)
            self._daily_df = pd.read_parquet(self._daily_path)
            if not isinstance(self._daily_df.index, pd.MultiIndex):
                self._daily_df.index.names = ["trade_date", "ts_code"]
            log.debug("Loaded stock_daily: %d rows", len(self._daily_df))
        return self._daily_df

    # ------------------------------------------------------------------
    # PIT fundamentals (fina_indicator)
    # ------------------------------------------------------------------

    def get_fundamentals_pit(self, code, asof_date, fields=None):
        """Return the latest visible financial fields for *code* as of *asof_date*.

        PIT logic:
          1. Filter rows where ts_code == code.
          2. Determine announcement date: prefer f_ann_date, fallback ann_date.
          3. Keep only rows with announcement_date <= asof_date.
          4. Among those, pick the row with the largest end_date.
          5. Return requested fields as a dict (NaN values become None).

        Args:
            code: tushare ts_code, e.g. "600000.SH"
            asof_date: date string "YYYY-MM-DD" or comparable
            fields: iterable of column names, defaults to DEFAULT_FIELDS

        Returns:
            dict {field: value} or {} if no data available.
        """
        df = self._load_fina()
        sub = df[df["ts_code"] == code]
        if sub.empty:
            return {}

        if fields is None:
            fields = DEFAULT_FIELDS

        # Determine announcement date column
        # fina_indicator has ann_date; some tables have f_ann_date
        if "f_ann_date" in sub.columns:
            ann_col = "f_ann_date"
        else:
            ann_col = "ann_date"

        # Convert asof_date to comparable string "YYYYMMDD"
        asof_ts = pd.Timestamp(asof_date)
        asof_str = asof_ts.strftime("%Y%m%d")

        # Filter: announcement date <= asof_date
        ann_series = sub[ann_col].astype(str)
        mask = ann_series <= asof_str
        visible = sub[mask]
        if visible.empty:
            return {}

        # Pick row with largest end_date (latest quarter)
        end_dates = visible["end_date"].astype(str)
        best_idx = end_dates.idxmax()
        row = visible.loc[best_idx]

        result = {}
        # Always include end_date for traceability
        end_val = row.get("end_date")
        result["end_date"] = str(end_val) if end_val is not None else None

        for f in fields:
            if f in row.index:
                val = row[f]
                if pd.isna(val):
                    result[f] = None
                else:
                    result[f] = val
            else:
                result[f] = None
        return result

    # ------------------------------------------------------------------
    # Daily PE (stock_daily)
    # ------------------------------------------------------------------

    def get_daily_pe(self, code, asof_date):
        """Return PE values for *code* as of *asof_date*.

        Uses the daily stock_daily parquet which contains pe and pe_ttm
        as daily snapshots — naturally anti-lookahead. We additionally
        require trade_date <= asof_date for safety.

        Args:
            code: tushare ts_code
            asof_date: date string "YYYY-MM-DD"

        Returns:
            dict {"dynamic_pe": pe_ttm, "static_pe": pe} or {}
        """
        df = self._load_daily()
        asof_ts = pd.Timestamp(asof_date)

        # Filter by code and trade_date <= asof_date
        if not isinstance(df.index, pd.MultiIndex):
            return {}

        codes_idx = df.index.get_level_values("ts_code")
        dates_idx = df.index.get_level_values("trade_date")

        mask = (codes_idx == code) & (dates_idx <= asof_ts)
        sub = df.loc[mask]
        if sub.empty:
            return {}

        # Take the last row (most recent trading day <= asof_date)
        last = sub.iloc[-1]
        pe = last.get("pe")
        pe_ttm = last.get("pe_ttm")

        result = {}
        if pe is not None and not pd.isna(pe):
            result["static_pe"] = float(pe)
        if pe_ttm is not None and not pd.isna(pe_ttm):
            result["dynamic_pe"] = float(pe_ttm)
        return result

    # ------------------------------------------------------------------
    # Scoring convenience
    # ------------------------------------------------------------------

    def get_fundamentals_for_scoring(self, codes, asof_date):
        """Build the fundamentals dict expected by 6+2 scoring.

        Returns:
            {code: {"dynamic_pe": float, "static_pe": float}}
        """
        result = {}
        for code in codes:
            pe_dict = self.get_daily_pe(code, asof_date)
            if pe_dict:
                result[code] = pe_dict
        return result

    def close(self):
        """Release cached DataFrames."""
        self._fina_df = None
        self._daily_df = None

    def __del__(self):
        self.close()
