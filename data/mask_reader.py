# coding: utf-8
"""涨跌停掩码版 reader —— 在 AstockParquetReader 基础上额外输出
up_limit / down_limit / suspend_type 三列，供策略层做 mask-aware 计算。

仅用于 T-20260829-001 掩码前置改造的对比实验，不影响原 reader。
"""
import pandas as pd

from data.astock_reader import AstockParquetReader


class MaskParquetReader(AstockParquetReader):
    """多读涨跌停/停牌字段，并在 load_window 中透传。"""

    _EXTRA_COLS = ["up_limit", "down_limit", "suspend_type", "pre_close"]

    def __init__(self, db_path=None, data_source="astock", adjustment="raw"):
        super().__init__(db_path, data_source, adjustment)
        # 重新以扩展列读取（super 已把 self._df 建好，但这里按扩展列重建）
        import pyarrow.parquet as _pq
        _present = set(_pq.ParquetFile(self.db_path).schema_arrow.names)
        _need = [
            "trade_date", "ts_code", "open", "high", "low", "close",
            "vol", "amount", "adj_factor", "circ_mv", "pe_ttm", "pb",
            "ps_ttm", "dv_ttm", "turnover_rate", "is_st",
        ] + self._EXTRA_COLS
        _read = [c for c in _need if c in _present]
        self._df = pd.read_parquet(self.db_path, columns=_read)
        if not isinstance(self._df.index, pd.MultiIndex):
            if "trade_date" in self._df.columns and "ts_code" in self._df.columns:
                self._df = self._df.set_index(["trade_date", "ts_code"])
        self._df.index.names = ["trade_date", "ts_code"]
        self._dates = pd.to_datetime(self._df.index.get_level_values("trade_date"))
        self._codes = self._df.index.get_level_values("ts_code")

    def load_window(self, codes, start_date, end_date):
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
            available = [
                "date", "open", "high", "low", "close", "vol", "amount", "circ_mv",
                "pe_ttm", "pb", "ps_ttm", "dv_ttm", "turnover_rate", "is_st",
                "up_limit", "down_limit", "suspend_type", "pre_close",
                "adj_factor",
            ]
            keep = [c for c in available if c in rows.columns]
            rows = rows[keep]
            out[code] = rows
        return out
