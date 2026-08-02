# coding: utf-8
"""benchmark_reader.py — read-only reader for benchmark_index.duckdb.

独立 reader：与 DuckDBDailyReader 解耦，只读 index_daily 表。
benchmark_index.duckdb 由 sync_xtquant_index_to_duckdb.py 维护，
schema 与 dat_day 不同，单独类避免混合两种 schema。

边界：
  * read_only；不 ATTACH；不写；不 mkdir。
  * 文件不存在 → 抛 FileNotFoundError，调用方自行降级（benchmark_available=False）。
"""
import datetime as _dt
import logging
import os

import duckdb

log = logging.getLogger(__name__)


class BenchmarkIndexReader(object):
    """读 benchmark_index.duckdb 的 index_daily 表。

    返回结构：list of (date_str, close_float)，按 trade_date 升序。
    """

    def __init__(self, db_path, source="xtquant"):
        if not os.path.isfile(db_path):
            raise FileNotFoundError("benchmark DuckDB not found: " + db_path)
        self.db_path = db_path
        self.source = source
        self._conn = duckdb.connect(db_path, read_only=True)
        self._db_mtime = _dt.datetime.fromtimestamp(
            os.path.getmtime(db_path)).isoformat(timespec="seconds")

    @property
    def db_mtime(self):
        return self._db_mtime

    def coverage(self, code):
        row = self._conn.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
            "FROM index_daily WHERE code = ? AND source = ?",
            [code, self.source]).fetchone()
        return {
            "code": code,
            "n_rows": int(row[0] or 0),
            "min_date": str(row[1]) if row[1] else "",
            "max_date": str(row[2]) if row[2] else "",
        }

    def load_series(self, code, start_date, end_date):
        """Return list of (date_str, close) tuples in ascending order.

        Empty list if no rows in the window.
        """
        rows = self._conn.execute(
            "SELECT trade_date, close FROM index_daily "
            "WHERE code = ? AND source = ? "
            "  AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date",
            [code, self.source, start_date, end_date]).fetchall()
        return [(str(d), float(c) if c is not None else None) for d, c in rows]

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def __del__(self):
        self.close()
