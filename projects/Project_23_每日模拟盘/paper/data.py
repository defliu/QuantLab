# -*- coding: utf-8 -*-
"""行情读取 —— 为模拟盘提供「当日 bar / 前收盘 / 交易日历」。

设计：一次性载入所需区间到内存（与 backtest 引擎一致），但对外暴露的是
「按 (date, code) 单点查询」接口，供逐日推进器消费。

为什么不直接用 data/astock_reader.py：
  - 它是回测导向的 load_window 批量接口，返回 {code: DataFrame} 结构
  - 模拟盘需要的是全市场按日切片（每天取当日所有持仓+候选的 bar），
    用 (date, code) 二维索引查询更直接
  - 数据源仍是同一个 D:/astock/daily/stock_daily.parquet，口径一致
"""
import os

import numpy as np
import pandas as pd

DAILY_PATH = "D:/astock/daily/stock_daily.parquet"
# 在线增量（Tushare 日刷，10 列精简版）：主库为周更快照，10 月行情只存在于增量库
INCR_PATH = ("D:/QuantLab/projects/Project_16_LightGBM股票大师/"
             "data_live/incremental_daily.parquet")

# 模拟盘需要的列（与 scan_rotate_cost_real.build_per_day 保持一致）
NEED_COLS = ["trade_date", "ts_code", "open", "high", "low", "close", "vol",
             "up_limit", "down_limit", "suspend_timing", "is_st", "pre_close"]

# 增量库列名 → 主库列名（增量是 Tushare 原始命名）
INCR_RENAME = {"volume": "vol", "preClose": "pre_close", "suspendFlag": "suspend_timing"}


def _limit_pct_series(codes, is_st):
    """按板块规则批量计算涨跌停幅度（向量化，供增量库补 up_limit/down_limit）。"""
    c = codes.str.split(".").str[0]
    pct = pd.Series(0.10, index=codes.index)
    pct[c.str.startswith(("300", "688"))] = 0.20
    pct[c.str.startswith("8") & (c.str.len() == 6)] = 0.30
    pct[is_st.astype(bool)] = 0.05
    return pct


class QuotePanel:
    """按 (date, code) 查询的行情面板（主库 + 在线增量合并）。

    主库 D:/astock/daily/stock_daily.parquet 是周更快照（当前截至 2026-09-30），
    10 月之后的数据只能来自 P16 的 incremental_daily.parquet（10 列精简版）。
    合并规则：同 (date, code) 以增量为准；增量缺 up_limit/down_limit/is_st 时按规则推算。
    """

    def __init__(self, start, end, lookback_days=60):
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        # 多取一段用于计算前收盘/峰值，避免边界缺数
        load_from = self.start - pd.Timedelta(days=lookback_days)
        cols = [c for c in NEED_COLS if c in self._available_cols()]
        df = pd.read_parquet(DAILY_PATH, columns=cols)
        # 主库 trade_date/ts_code 可能存于 MultiIndex 或普通列，两种都要兼容
        if not isinstance(df.index, pd.RangeIndex):
            df = df.reset_index()
        if "trade_date" not in df.columns or "ts_code" not in df.columns:
            raise KeyError("行情库缺少 trade_date/ts_code，实际列=%s，索引=%s"
                           % (list(df.columns)[:10], df.index.names))
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["ts_code"] = df["ts_code"].astype(str)
        df = df[(df["trade_date"] >= load_from) & (df["trade_date"] <= self.end)]

        # ---- 增量合并 ----
        self.incr_dates = []
        inc = self._load_incremental(load_from, self.end)
        if inc is not None and len(inc):
            self.incr_dates = sorted(inc["trade_date"].unique())
            df = df[~df.set_index(["trade_date", "ts_code"]).index.isin(
                inc.set_index(["trade_date", "ts_code"]).index)]
            df = pd.concat([df, inc], ignore_index=True)

        df = df.sort_values(["ts_code", "trade_date"])

        # 前收盘：优先用库内 pre_close（原始值，与涨跌停口径一致）；
        # 缺失时才用 shift(1) 兜底
        if "pre_close" in df.columns:
            df["prev_close"] = df["pre_close"]
            need_fill = df["prev_close"].isna()
            if need_fill.any():
                fb = df.groupby("ts_code")["close"].shift(1)
                df.loc[need_fill, "prev_close"] = fb[need_fill]
        else:
            df["prev_close"] = df.groupby("ts_code")["close"].shift(1)

        self._df = df.set_index(["trade_date", "ts_code"]).sort_index()
        self._codes = set(df["ts_code"].unique())
        self._dates = sorted(df["trade_date"].unique())

    @staticmethod
    def _available_cols():
        """读取 parquet 的列名（避免整表载入）。"""
        import pyarrow.parquet as pq
        return set(pq.ParquetFile(DAILY_PATH).schema.names)

    @staticmethod
    def _load_incremental(load_from, load_to):
        """加载在线增量库并对齐主库列口径；文件缺失返回 None。

        增量库只有 10 列（open/high/low/close/volume/amount/preClose/suspendFlag），
        缺 up_limit/down_limit/is_st —— 前两者按板块规则从 preClose 推算，
        is_st 增量无此信息，按 False 处理（涨跌停幅度用 10%/20%/30% 规则，
        对 ST 会放宽到 5% 以上的判定，属已知近似，报告需注明）。
        """
        if not os.path.isfile(INCR_PATH):
            return None
        inc = pd.read_parquet(INCR_PATH)
        if len(inc) == 0:
            return None
        inc = inc.rename(columns=INCR_RENAME)
        inc["trade_date"] = pd.to_datetime(inc["trade_date"])
        inc["ts_code"] = inc["ts_code"].astype(str)
        inc = inc[(inc["trade_date"] >= load_from) & (inc["trade_date"] <= load_to)]
        if len(inc) == 0:
            return None

        if "is_st" not in inc.columns:
            inc["is_st"] = False
        if "up_limit" not in inc.columns or "down_limit" not in inc.columns:
            pct = _limit_pct_series(inc["ts_code"], inc["is_st"])
            pre = inc["pre_close"].astype(float)
            inc["up_limit"] = (pre * (1 + pct)).round(2)
            inc["down_limit"] = (pre * (1 - pct)).round(2)
        # 补齐主库口径列，缺失的置空
        for c in NEED_COLS:
            if c not in inc.columns:
                inc[c] = np.nan
        return inc[NEED_COLS]

    def bar(self, date, code):
        """返回 dict（含 open/high/low/close/vol/prev_close/is_st/code），缺失返回 None。"""
        try:
            row = self._df.loc[(pd.Timestamp(date), code)]
        except KeyError:
            return None
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        d = row.to_dict()
        d["code"] = code
        return d

    def trading_dates(self, start=None, end=None):
        """返回区间内交易日列表（升序）。"""
        s = pd.Timestamp(start) if start else self.start
        e = pd.Timestamp(end) if end else self.end
        return [d for d in self._dates if s <= d <= e]

    def has(self, date, code):
        return (pd.Timestamp(date), code) in self._df.index

    def universe(self):
        return self._codes
