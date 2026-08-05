# coding: utf-8
"""ROE as-of loader — thin wrapper over factors/fina (shared cache).

Kept for backward compatibility: atr_lowvol.py calls get_roe_asof(code, date).
The actual load + point-in-time logic lives in factors/fina.py so that
roe/fcff/ocfps all share ONE process-wide cache.
"""
from factors.fina import get_fina_asof as _get_fina_asof


def get_roe_asof(code, date, parquet_path=None):
    """Return ROE (%) as of `date` for `code`, or None if unavailable."""
    return _get_fina_asof(code, date, "roe", parquet_path)
