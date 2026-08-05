# coding: utf-8
"""General fundamental as-of loader (point-in-time, no look-ahead).

Source: E:/astock/finance/fina_indicator.parquet
Provides:
  - get_fina_asof(code, date, field): latest value of any field as of date
  - is_roe_stable(code, date, n):     ROE>0 for the last n reported quarters
                                       (or all available if fewer than n)

Only the columns actually needed are loaded (roe/fcff/fcfe/ocfps) into a
process-wide per-code cache. factors/roe.py delegates get_roe_asof here so
there is a single cache (no double-load of the 300k-row parquet).
"""
import numpy as np
import pandas as pd

ASTOCK_FINA_PATH = "E:/astock/finance/fina_indicator.parquet"
_FIELDS = ["roe", "fcff", "fcfe", "ocfps"]

_CACHE = None


class _FinaCache(object):
    def __init__(self, parquet_path):
        df = pd.read_parquet(parquet_path,
                             columns=["ts_code", "end_date"] + _FIELDS)
        df["ed"] = (df["end_date"].astype(str)
                    .str.replace("-", "", regex=False).str[:8])
        self.by_code = {}
        for code, g in df.groupby("ts_code"):
            g = g.sort_values("ed")
            rec = {"ed": g["ed"].values}
            for f in _FIELDS:
                rec[f] = g[f].values.astype(float)
            self.by_code[code] = rec

    def asof(self, code, date, field):
        if code not in self.by_code:
            return None
        rec = self.by_code[code]
        ed = rec["ed"]
        idx = int(np.searchsorted(ed, str(date).replace("-", "")[:8])) - 1
        if idx < 0:
            return None
        val = rec[field][idx]
        if val != val:  # NaN
            return None
        return float(val)

    def roe_stable(self, code, date, n):
        if code not in self.by_code:
            return False
        rec = self.by_code[code]
        ed = rec["ed"]
        idx = int(np.searchsorted(ed, str(date).replace("-", "")[:8])) - 1
        if idx < 0:
            return False
        lo = max(0, idx - n + 1)
        window = rec["roe"][lo:idx + 1]
        if len(window) == 0:
            return False
        return bool(np.all(window > 0))


def get_fina_asof(code, date, field, parquet_path=None):
    """Latest `field` value (e.g. 'roe'/'fcff'/'fcfe'/'ocfps') as of `date`."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _FinaCache(parquet_path or ASTOCK_FINA_PATH)
    return _CACHE.asof(code, date, field)


def is_roe_stable(code, date, n=8, parquet_path=None):
    """True if ROE>0 for the last `n` reported quarters (or all if fewer)."""
    global _CACHE
    if _CACHE is None:
        _CACHE = _FinaCache(parquet_path or ASTOCK_FINA_PATH)
    return _CACHE.roe_stable(code, date, n)
