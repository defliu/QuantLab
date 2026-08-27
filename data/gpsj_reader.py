# coding: utf-8
"""Gpsj duckdb reader — 备用数据源 (E:\\huicexitong\\runtime\\sj\\gpsj.duckdb).

Duck-typed match for AstockParquetReader / DuckDBDailyReader 4-method interface:
  load_window / trading_calendar / coverage / close(code, date)

口径对齐 (已验证 2025-01-06 / 2024-06-03 / 2019-05-06 与 E:/astock 同股同日逐字段一致):
  - adj_factor / 换手率 / 流通市值 / 成交量 / 成交额 / pe_ttm / pb 与 astock 完全相同 (同源 tushare)
  - astock close = 不复权价 == gpsj '不复权_收盘价'；gpsj '收盘价' 是前复权价(不用!)
  - 本 reader 一律取 gpsj '不复权_*' 列 + '复权因子'，adjustment 逻辑与 astock_reader 一致
    (raw 原样 / hfq=raw*adj / qfq=raw*(adj/latest_adj))
覆盖: 日线 1990-12-19 ~ 2026-04-03 (1376万行)；全市场完整覆盖仅 2015-01 起
      (2015 前仅 ~160-190 只/日，非全市场，仅作参考)
"""
import datetime as _dt
import logging

import duckdb
import pandas as pd

log = logging.getLogger(__name__)

DATA_SOURCE_GPSJ = "gpsj"
GPSJ_DB_PATH = "E:/huicexitong/runtime/sj/gpsj.duckdb"

# gpsj 中文列 -> 框架标准列 (gpsj '收盘价' 是 qfq，一律用 '不复权_*')
_COL_MAP = {
    "不复权_开盘价": "open",
    "不复权_最高价": "high",
    "不复权_最低价": "low",
    "不复权_收盘价": "close",
    "成交量(手)": "vol",
    "成交额(千元)": "amount",
    "复权因子": "adj_factor",
    "换手率(%)": "turnover_rate",
    "流通市值(万元)": "circ_mv",
    "市盈率TTM": "pe_ttm",
    "市净率": "pb",
    "市销率TTM": "ps_ttm",
    "股息率TTM(%)": "dv_ttm",
    "ST": "is_st",
}
_GPSJ_COLS = list(_COL_MAP.keys())


class GpsjDuckDBReader(object):
    """Read-only duckdb reader for huicexitong gpsj.duckdb daily data."""

    def __init__(self, db_path=None, data_source=DATA_SOURCE_GPSJ, adjustment="raw"):
        if adjustment not in ("raw", "qfq", "hfq"):
            raise ValueError("adjustment must be one of raw/qfq/hfq, got: %s" % adjustment)
        if db_path is None:
            db_path = GPSJ_DB_PATH
        self.db_path = db_path
        self.data_source = data_source
        self.adjustment = adjustment
        self._con = duckdb.connect(db_path, read_only=True)
        self._table = 'gpsj.daily_data."日线数据"'
        # 预取范围元数据
        row = self._con.execute(
            'SELECT min(交易日期), max(交易日期), count(DISTINCT 股票代码) FROM %s' % self._table
        ).fetchone()
        self._min_date = row[0].strftime("%Y-%m-%d") if row[0] else None
        self._max_date = row[1].strftime("%Y-%m-%d") if row[1] else None
        self._n_codes = int(row[2] or 0)

    # ---------- 内部工具 ----------
    def _sql_cols(self):
        return ", ".join('"%s" AS %s' % (g, s) for g, s in _COL_MAP.items())

    def _apply_adjustment(self, df):
        if self.adjustment != "raw" and "adj_factor" in df.columns:
            adj = df["adj_factor"].values
            if self.adjustment == "hfq":
                for col in ("open", "high", "low", "close"):
                    df[col] = df[col] * adj
            elif self.adjustment == "qfq":
                latest_adj = adj[-1] if len(adj) else 0.0
                if latest_adj > 0:
                    for col in ("open", "high", "low", "close"):
                        df[col] = df[col] * (adj / latest_adj)
        return df

    # ---------- duck-typed 4 方法 ----------
    def load_window(self, codes, start_date, end_date):
        """Load OHLCV for given codes within [start_date, end_date].

        Returns dict: {code: DataFrame(date, open, high, low, close, vol, amount,
                                       circ_mv, pe_ttm, pb, ps_ttm, dv_ttm,
                                       turnover_rate, is_st, adj_factor)}
        """
        if not codes:
            raise ValueError("codes is empty")
        sel = self._sql_cols()
        placeholders = ", ".join("?" for _ in codes)
        q = ("SELECT 股票代码 AS ts_code, 交易日期, %s FROM %s "
             "WHERE 股票代码 IN (%s) AND 交易日期 >= ? AND 交易日期 <= ?"
             % (sel, self._table, placeholders))
        params = list(codes) + [start_date, end_date]
        df = self._con.execute(q, params).fetchdf()
        if df.empty:
            raise ValueError(
                "requested range [%s, %s] has no data for %d codes"
                % (start_date, end_date, len(codes))
            )
        df["date"] = pd.to_datetime(df["交易日期"]).dt.strftime("%Y-%m-%d")
        df = df.drop(columns=["交易日期"])
        out = {}
        for code, grp in df.groupby("ts_code", sort=False):
            rows = grp.sort_values("date").reset_index(drop=True).copy()
            self._apply_adjustment(rows)
            out[code] = rows
        return out

    def trading_calendar(self, start_date, end_date):
        """Return sorted list of trading date strings in [start_date, end_date]."""
        rows = self._con.execute(
            "SELECT DISTINCT 交易日期 FROM %s WHERE 交易日期 >= ? AND 交易日期 <= ? ORDER BY 交易日期"
            % self._table, [start_date, end_date]).fetchall()
        return [r[0].strftime("%Y-%m-%d") for r in rows]

    def coverage(self, codes=None, start_date=None, end_date=None):
        """Return coverage dict; optionally filter to specific codes/date range."""
        cov = {
            "data_source": self.data_source,
            "min_date": self._min_date,
            "max_date": self._max_date,
            "n_codes": self._n_codes,
            "db_mtime": _dt.datetime.fromtimestamp(
                __import__("os").path.getmtime(self.db_path)
            ).isoformat(timespec="seconds"),
        }
        if codes is not None:
            sd = start_date or self._min_date
            ed = end_date or self._max_date
            placeholders = ", ".join("?" for _ in codes)
            rows = self._con.execute(
                "SELECT DISTINCT 股票代码 FROM %s WHERE 交易日期 >= ? AND 交易日期 <= ? AND 股票代码 IN (%s)"
                % (self._table, placeholders), [sd, ed] + list(codes)).fetchall()
            present = set(r[0] for r in rows)
            missing = [c for c in codes if c not in present]
            cov["universe_coverage"] = {
                "universe_size": len(codes),
                "codes_with_data": len(present),
                "codes_missing": missing,
                "missing_count": len(missing),
            }
        return cov

    def close(self, code=None, date=None):
        """Close price accessor (code, date) or no-op cleanup (no args)."""
        if code is not None and date is not None:
            row = self._con.execute(
                "SELECT 不复权_收盘价, 复权因子 FROM %s WHERE 交易日期 = ? AND 股票代码 = ?"
                % self._table, [date, code]).fetchone()
            if not row:
                return None
            val, adj = float(row[0]), float(row[1])
            if self.adjustment == "hfq":
                val = val * adj
            elif self.adjustment == "qfq":
                latest = self._con.execute(
                    "SELECT 复权因子 FROM %s WHERE 股票代码 = ? ORDER BY 交易日期 DESC LIMIT 1"
                    % self._table, [code]).fetchone()
                if latest and latest[0] > 0:
                    val = val * (adj / float(latest[0]))
            return val
        return None

    def close_conn(self):
        try:
            self._con.close()
        except Exception:
            pass

    def __del__(self):
        self.close_conn()