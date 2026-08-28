# coding: utf-8
"""Astock parquet reader — duck-typed match for DuckDBDailyReader.

Data source: E:\\astock\\daily\\stock_daily.parquet (tushare daily data, 2009+)
            + E:\\astock\\Updatedata\\<week>\\stock_daily.parquet (增量，研发/回测使用)
Implements the same 4-method duck-typed interface as DuckDBDailyReader:
  load_window / trading_calendar / coverage / close(code, date)

Constraints:
  - Read-only; no writes to E:\\astock\\
  - Output columns aligned with DuckDBDailyReader: date, open, high, low, close, vol, amount
  - Code format: tushare ts_code (e.g. "000001.SZ")
  - 增量合并规则：主仓 + 各周增量按 (trade_date, ts_code) 去重，增量优先（同日期以增量为准）
"""
import datetime as _dt
import logging
import os

import pandas as pd

log = logging.getLogger(__name__)

DATA_SOURCE_ASTOCK = "astock"
ASTOCK_DAILY_PATH = "E:/astock/daily/stock_daily.parquet"


def _merge_update_daily(main_df, main_idx_type):
    """将 Updatedata 下各周增量 daily 合并进主仓 DataFrame（增量优先）。

    主仓为 MultiIndex (trade_date, ts_code) 或普通列两种格式均可；
    增量统一为普通列格式（trade_date/ts_code/open/high/...）。
    合并后返回与 main_df 相同索引结构（MultiIndex）的 DataFrame。
    """
    try:
        import sys
        # data_config.py 位于 Project_16 项目目录
        sys.path.insert(0, r"D:\QuantLab\projects\Project_16_LightGBM股票大师")
        import data_config as DC
    except Exception as e:  # pragma: no cover
        log.warning("data_config 导入失败，跳过增量合并: %s", e)
        return main_df
    weeks = DC.list_update_weeks()
    if not weeks:
        return main_df
    parts = [main_df]
    for week_dir in weeks:
        fp = os.path.join(week_dir, "stock_daily.parquet")
        if not os.path.isfile(fp):
            continue
        try:
            inc = pd.read_parquet(fp)
        except Exception as e:  # pragma: no cover
            log.warning("增量 parquet 读取失败 %s: %s", fp, e)
            continue
        if "trade_date" not in inc.columns or "ts_code" not in inc.columns:
            continue
        # 增量转 MultiIndex，与主仓统一
        inc = inc.set_index(["trade_date", "ts_code"])
        inc.index.names = ["trade_date", "ts_code"]
        parts.append(inc)
    combined = pd.concat(parts)
    # 按 (trade_date, ts_code) 去重，保留最后一个（后追加的增量在后，即增量优先）
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined.sort_index()
    return combined


class AstockParquetReader(object):
    """Read-only parquet reader for astock daily data.

    Duck-typed to match DuckDBDailyReader interface so the engine
    can consume either reader without code changes.

    Parquet has MultiIndex (trade_date, ts_code).
    All date filtering works via index level accessors.
    """

    def __init__(self, db_path=None, data_source=DATA_SOURCE_ASTOCK, adjustment="raw"):
        if adjustment not in ("raw", "qfq", "hfq"):
            raise ValueError("adjustment must be one of raw/qfq/hfq, got: %s" % adjustment)
        if db_path is None:
            db_path = ASTOCK_DAILY_PATH
        if not os.path.isfile(db_path):
            raise FileNotFoundError("astock parquet not found: " + db_path)
        self.db_path = db_path
        self.data_source = data_source
        self.adjustment = adjustment
        self.wal_detected = False
        self.wal_warning_message = ""
        # 仅读取回测所需列，降低内存占用（原始表约 1400w 行 × 34 列）。
        import pyarrow.parquet as _pq
        _present = set(_pq.ParquetFile(db_path).schema_arrow.names)
        _need_cols = [
            "trade_date", "ts_code", "open", "high", "low", "close",
            "vol", "amount", "adj_factor", "circ_mv", "pe_ttm", "pb",
            "ps_ttm", "dv_ttm", "turnover_rate", "is_st",
        ]
        _read_cols = [c for c in _need_cols if c in _present]
        self._df = pd.read_parquet(db_path, columns=_read_cols)
        # 实际 parquet 以 trade_date / ts_code 作为普通列存储（无 pandas 索引元数据）。
        # 构造 (trade_date, ts_code) MultiIndex 以匹配 duck-typed 契约，
        # 同时兼容“已是 MultiIndex”的历史格式。
        if not isinstance(self._df.index, pd.MultiIndex):
            if "trade_date" in self._df.columns and "ts_code" in self._df.columns:
                self._df = self._df.set_index(["trade_date", "ts_code"])
            else:
                raise ValueError(
                    "astock parquet 缺少 trade_date/ts_code 列，无法构建索引: " + db_path)
        self._df.index.names = ["trade_date", "ts_code"]
        # 增量合并（2026-08-28 新增）：主仓 + Updatedata 各周，增量优先
        self._df = _merge_update_daily(self._df, None)
        self._dates = pd.to_datetime(self._df.index.get_level_values("trade_date"))
        self._codes = self._df.index.get_level_values("ts_code")
        self._coverage_cache = None

    def load_window(self, codes, start_date, end_date):
        """Load OHLCV for given codes within [start_date, end_date].

        Returns dict: {code: DataFrame(date, open, high, low, close, vol, amount)}
        aligned with DuckDBDailyReader output.
        """
        if not codes:
            raise ValueError("codes is empty")
        dates = self._dates
        codes_idx = self._codes
        mask = (
            (dates >= pd.Timestamp(start_date))
            & (dates <= pd.Timestamp(end_date))
            & (codes_idx.isin(codes))
        )
        sub = self._df.loc[mask].copy()
        if sub.empty:
            raise ValueError(
                "requested range [%s, %s] has no data for %d codes"
                % (start_date, end_date, len(codes))
            )
        out = {}
        for code, grp in sub.groupby(level="ts_code"):
            rows = grp.droplevel("ts_code").reset_index()
            rows["date"] = pd.to_datetime(rows["trade_date"]).dt.strftime("%Y-%m-%d")
            rows = rows.sort_values("date").reset_index(drop=True)
            
            if self.adjustment != "raw" and "adj_factor" in rows.columns:
                adj_factor = rows["adj_factor"].values
                price_cols = ["open", "high", "low", "close"]
                
                if self.adjustment == "hfq":
                    for col in price_cols:
                        rows[col] = rows[col] * adj_factor
                elif self.adjustment == "qfq":
                    latest_adj = adj_factor[-1]
                    if latest_adj > 0:
                        for col in price_cols:
                            rows[col] = rows[col] * (adj_factor / latest_adj)
            
            available = ["date", "open", "high", "low", "close", "vol", "amount", "circ_mv",
                         "pe_ttm", "pb", "ps_ttm", "dv_ttm", "turnover_rate", "is_st",
                         "adj_factor"]  # adj_factor 供策略侧还原真实价（真实价 = 复权价/adj_factor）
            keep = [c for c in available if c in rows.columns]
            rows = rows[keep]
            out[code] = rows
        return out

    def trading_calendar(self, start_date, end_date):
        """Return sorted list of trading date strings in [start_date, end_date]."""
        dates = self._dates
        mask = (
            (dates >= pd.Timestamp(start_date))
            & (dates <= pd.Timestamp(end_date))
        )
        unique_dates = sorted(dates[mask].unique())
        return [d.strftime("%Y-%m-%d") for d in unique_dates]

    def coverage(self, codes=None, start_date=None, end_date=None):
        """Return coverage dict; optionally filter to specific codes/date range."""
        if self._coverage_cache is None:
            self._coverage_cache = {
                "data_source": self.data_source,
                "min_date": self._dates.min().strftime("%Y-%m-%d"),
                "max_date": self._dates.max().strftime("%Y-%m-%d"),
                "n_codes": self._codes.nunique(),
                "n_rows_after_dedup": len(self._df),
                "dedup_count": 0,
                "db_mtime": _dt.datetime.fromtimestamp(
                    os.path.getmtime(self.db_path)
                ).isoformat(timespec="seconds"),
            }
        cov = dict(self._coverage_cache)
        if codes is not None:
            sd = pd.Timestamp(start_date) if start_date else pd.Timestamp(cov["min_date"])
            ed = pd.Timestamp(end_date) if end_date else pd.Timestamp(cov["max_date"])
            dates = self._dates
            codes_idx = self._codes
            mask = (
                (dates >= sd)
                & (dates <= ed)
                & (codes_idx.isin(codes))
            )
            present = set(codes_idx[mask].unique())
            missing = [c for c in codes if c not in present]
            cov["universe_coverage"] = {
                "universe_size": len(codes),
                "codes_with_data": len(present),
                "codes_missing": missing,
                "missing_count": len(missing),
            }
        return cov

    def close(self, code=None, date=None):
        """Close price accessor (code, date) or no-op cleanup (no args).

        DuckDBDailyReader.close() closes the DB connection; astock parquet
        is in-memory so no-op is correct. Scripts call reader.close() in
        finally blocks, so this must tolerate zero-arg calls.
        """
        if code is not None and date is not None:
            ts = pd.Timestamp(date)
            try:
                val = self._df.loc[(ts, code), "close"]
                if self.adjustment != "raw" and "adj_factor" in self._df.columns:
                    adj = self._df.loc[(ts, code), "adj_factor"]
                    if self.adjustment == "hfq":
                        val = val * adj
                    elif self.adjustment == "qfq":
                        latest_adj = self._df.loc[(self._dates.max(), code), "adj_factor"] if code in self._df.index.get_level_values("ts_code") else adj
                        if latest_adj > 0:
                            val = val * (adj / latest_adj)
                return float(val)
            except KeyError:
                return None
        return None

    def __del__(self):
        self._df = None
        self._dates = None
        self._codes = None
        self._coverage_cache = None
