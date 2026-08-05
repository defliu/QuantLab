# coding: utf-8
"""Industry map loader — code -> 申万 industry, for the industry_cap overlay.

Source: E:/astock/basic/stock_basic.parquet (Tushare 口径, `industry` 字段).
Result is cached process-wide so repeated backtests don't re-read the parquet.
"""
import os

import pandas as pd

ASTOCK_BASIC_PATH = "E:/astock/basic/stock_basic.parquet"

_CACHE = {}


def load_industry_map(parquet_path=None, refresh=False):
    """Return dict {ts_code: industry} (industry may be '' if missing).

    Cached by absolute path. Pass refresh=True to force re-read.
    """
    path = parquet_path or ASTOCK_BASIC_PATH
    abspath = os.path.abspath(path)
    if (not refresh) and abspath in _CACHE:
        return _CACHE[abspath]
    if not os.path.isfile(path):
        raise FileNotFoundError("stock_basic parquet not found: " + path)
    df = pd.read_parquet(path, columns=["ts_code", "industry"])
    out = {}
    for _, row in df.iterrows():
        code = row.get("ts_code")
        if code is None:
            continue
        ind = row.get("industry")
        out[str(code)] = str(ind) if ind is not None else ""
    _CACHE[abspath] = out
    return out


def clear_cache():
    _CACHE.clear()
