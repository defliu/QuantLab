# coding=utf-8
"""数据源统一接口 — 对接原有数据源"""

import os
import pandas as pd
import numpy as np
from typing import List, Optional, Dict


# 数据源路径配置
ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
ASTOCK_FINANCE = "D:/astock/finance/fina_indicator.parquet"
ASTOCK_BASIC = "D:/astock/basic/stock_basic.parquet"
# 2026-08-29：F 盘不存在，quantifydata.duckdb 已缺失。改为环境变量可配置，
# 未配置时 duckdb 分支延迟报错（不影响默认的 astock 分支）。
DUCKDB_PATH = os.environ.get(
    "DUCKDB_PATH", "F:/金策智算/_internal/databases/duckdb/quantifydata.duckdb")


class DataFeed:
    """数据源统一接口"""

    def __init__(self, source="astock"):
        """
        Args:
            source: 数据源 "astock" / "duckdb"
        """
        self.source = source
        self._cache = {}

    def get_daily(self, codes: List[str] = None,
                  start_date: str = None, end_date: str = None,
                  fields: List[str] = None) -> pd.DataFrame:
        """获取日线数据

        Returns:
            MultiIndex DataFrame (date, code) -> [open, high, low, close, vol, amount, ...]
        """
        if self.source == "astock":
            return self._get_astock_daily(codes, start_date, end_date, fields)
        elif self.source == "duckdb":
            return self._get_duckdb_daily(codes, start_date, end_date, fields)
        else:
            raise ValueError("Unknown source: %s" % self.source)

    def get_universe(self, end_date: str = None, top_n: int = 500) -> List[str]:
        """获取股票池（按成交额排序）"""
        df = self.get_daily(end_date=end_date)
        if df.empty:
            return []

        # 取最近交易日
        dates = sorted(df.index.get_level_values("date").unique())
        if end_date:
            end_d = pd.Timestamp(end_date).date()
            dates = [d for d in dates if (d.date() if hasattr(d, 'date') else d) <= end_d]
        if not dates:
            return []
        latest = dates[-1]

        # 按成交额排序
        day_data = df.loc[latest]
        if "amount" in day_data.columns:
            ranked = day_data["amount"].sort_values(ascending=False)
            return list(ranked.head(top_n).index)
        return list(day_data.index[:top_n])

    def get_financials(self, codes: List[str] = None) -> pd.DataFrame:
        """获取财务数据"""
        if self.source == "astock":
            return self._get_astock_financials(codes)
        return pd.DataFrame()

    def _get_astock_daily(self, codes, start_date, end_date, fields):
        """从 astock parquet 获取日线"""
        if "astock_daily" not in self._cache:
            if not os.path.exists(ASTOCK_DAILY):
                raise FileNotFoundError("astock parquet not found: %s" % ASTOCK_DAILY)
            self._cache["astock_daily"] = pd.read_parquet(ASTOCK_DAILY)

        df = self._cache["astock_daily"].copy()

        # Reset index if needed
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()

        # Filter by codes
        if codes:
            df = df[df["ts_code"].isin(codes)]

        # Filter by date range
        # 2026-08-29 修正：原写法把 pandas Timestamp 与 datetime.date 直接比较
        # （Timestamp 也有 .date 属性，hasattr 判断恒为 True），pandas 3.x 起
        # 抛 TypeError: Cannot compare Timestamp with datetime.date，整个
        # astock 分支不可用。统一转成 Timestamp 再比较。
        if start_date or end_date:
            _td = pd.to_datetime(df["trade_date"])
            _mask = pd.Series(True, index=df.index)
            if start_date:
                _mask = _mask & (_td >= pd.Timestamp(start_date))
            if end_date:
                _mask = _mask & (_td <= pd.Timestamp(end_date))
            df = df[_mask]

        # Select fields
        if fields:
            keep = ["trade_date", "ts_code"] + [f for f in fields if f in df.columns]
            df = df[keep]

        # Set MultiIndex
        df = df.set_index(["trade_date", "ts_code"])
        df.index.names = ["date", "code"]
        return df

    def _get_duckdb_daily(self, codes, start_date, end_date, fields):
        """从 DuckDB 获取日线"""
        # 2026-08-29 修正：模块实际位于 data/duckdb_reader.py（扁平布局），
        # 原 backtest.data_tools.duckdb_reader 是不存在的包路径。
        if not os.path.isfile(DUCKDB_PATH):
            raise FileNotFoundError(
                "duckdb 数据源不可用: %s\n"
                "  - 设置环境变量 DUCKDB_PATH 指向实际 duckdb 文件，"
                "或改用 DataFeed('astock')。" % DUCKDB_PATH)
        from data.duckdb_reader import DuckDBDailyReader
        reader = DuckDBDailyReader(DUCKDB_PATH)
        try:
            data = reader.load_window(codes or [], start_date or "2018-01-01", end_date or "2026-12-31")
            # Convert to MultiIndex DataFrame
            frames = []
            for code, df in data.items():
                df["code"] = code
                df["date"] = pd.to_datetime(df["date"])
                frames.append(df)
            if frames:
                result = pd.concat(frames)
                result = result.set_index(["date", "code"])
                return result
        finally:
            reader.close()
        return pd.DataFrame()

    def _get_astock_financials(self, codes):
        """从 astock parquet 获取财务数据"""
        if not os.path.exists(ASTOCK_FINANCE):
            return pd.DataFrame()

        df = pd.read_parquet(ASTOCK_FINANCE)
        if codes:
            df = df[df["ts_code"].isin(codes)]
        return df


def get_panel(start_date="2018-01-01", end_date="2026-12-31",
              top_n=500) -> tuple:
    """获取面板数据（兼容原有接口）

    Returns:
        (panel, fin_ffill) tuple
    """
    feed = DataFeed("astock")

    # Get universe
    codes = feed.get_universe(end_date, top_n)
    log("Universe: %d codes" % len(codes))

    # Get daily data
    daily = feed.get_daily(codes, start_date, end_date)
    log("Daily data: %s" % str(daily.shape))

    # Build panel
    panel = pd.DataFrame({
        "close": daily["close"] if "close" in daily.columns else np.nan,
        "open": daily["open"] if "open" in daily.columns else np.nan,
        "volume": daily["vol"] if "vol" in daily.columns else daily.get("volume", np.nan),
        "amount": daily["amount"] if "amount" in daily.columns else np.nan,
        "pe_ttm": daily["pe_ttm"] if "pe_ttm" in daily.columns else np.nan,
        "pb": daily["pb"] if "pb" in daily.columns else np.nan,
        "circ_mv": daily["circ_mv"] if "circ_mv" in daily.columns else np.nan,
        "pct_chg": daily["pct_chg"] if "pct_chg" in daily.columns else np.nan,
        "turnover_rate": daily["turnover_rate"] if "turnover_rate" in daily.columns else np.nan,
    })

    # Get financials
    fin = feed.get_financials(codes)
    if not fin.empty:
        fin_pivot = fin.pivot_table(
            index="end_date", columns="ts_code",
            values=["roe", "grossprofit_margin", "netprofit_margin", "bps", "ocfps"],
        )
        trade_dates = sorted(panel.index.get_level_values("date").unique())
        all_dates_idx = pd.DatetimeIndex(trade_dates)
        fin_ffill = fin_pivot.reindex(all_dates_idx, method="ffill")
    else:
        fin_ffill = pd.DataFrame()

    log("Panel: %s, Fin: %s" % (panel.shape, fin_ffill.shape))
    return panel, fin_ffill


def log(msg):
    print("[DataFeed] %s" % msg)


if __name__ == "__main__":
    # 测试
    feed = DataFeed("astock")
    codes = feed.get_universe("2026-06-18", 10)
    print("Universe (top 10):", codes)

    df = feed.get_daily(codes, "2026-06-01", "2026-06-18")
    print("Daily shape:", df.shape)
    print("Columns:", list(df.columns))
