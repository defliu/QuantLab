# coding: utf-8
"""板块（行业）RPS 计算模块 —— RPS 主升浪策略 v1.1 板块层。

从 E:/astock/basic/stock_basic.parquet 加载行业归属，计算行业 RPS。
行业 RPS = 行业指数过去 N 日涨幅在全行业的百分位（0-100）。

用法：
  from strategy.sector_rps import SectorRPS
  sr = SectorRPS(stock_basic_path, industry_map_path=None)
  sr.load()
  sector_rps = sr.compute_sector_rps(market_window, window=20)
  # 返回 {industry: rps_percentile}

行业归属来源（优先级）：
  1. 显式传入 industry_map（{code: industry}）
  2. stock_basic.parquet 的 industry 字段
"""
import logging

log = logging.getLogger(__name__)


class SectorRPS:
    def __init__(self, stock_basic_path=None, industry_map=None):
        self.stock_basic_path = stock_basic_path or "E:/astock/basic/stock_basic.parquet"
        self._industry_map = industry_map  # {code: industry}
        self._loaded = False

    def load(self):
        """加载行业归属（静态映射，行业归属在 A 股基本不变）。"""
        if self._loaded:
            return
        if self._industry_map is None:
            import pyarrow.parquet as pq
            t = pq.read_table(self.stock_basic_path,
                              columns=["ts_code", "industry"])
            df = t.to_pandas()
            # 过滤无行业的股票
            df = df[df["industry"].notna() & (df["industry"].str.strip() != "")]
            self._industry_map = dict(zip(df["ts_code"], df["industry"]))
        self._loaded = True

    def code_to_industry(self, code):
        """返回股票所属行业，未知返回 None。"""
        return self._industry_map.get(code)

    def compute_sector_returns(self, market_window, window):
        """计算每个行业的等权涨幅。

        Args:
            market_window: {code: DataFrame}（需含 close 列）
            window: 计算窗口（交易日数）

        Returns:
            {industry: 等权平均涨幅}（至少 1 只有效股票）
        """
        self.load()
        sector_rets = {}
        sector_cnt = {}
        for code, df in market_window.items():
            if df is None or len(df) < window + 1:
                continue
            ind = self.code_to_industry(code)
            if not ind:
                continue
            try:
                close = df["close"]
                end_price = float(close.iloc[-1])
                start_price = float(close.iloc[-window - 1])
                if start_price <= 0:
                    continue
                ret = (end_price / start_price) - 1.0
            except Exception:
                continue
            sector_rets[ind] = sector_rets.get(ind, 0.0) + ret
            sector_cnt[ind] = sector_cnt.get(ind, 0) + 1

        out = {}
        for ind, total in sector_rets.items():
            n = sector_cnt[ind]
            if n > 0:
                out[ind] = total / n  # 等权平均
        return out

    def compute_sector_rps(self, market_window, window):
        """计算行业 RPS（百分位 0-100）。

        Args:
            market_window: {code: DataFrame}
            window: 计算窗口

        Returns:
            {industry: rps_percentile}，最强行业 100，最弱 ~0
        """
        sector_rets = self.compute_sector_returns(market_window, window)
        if not sector_rets:
            return {}
        sorted_ind = sorted(sector_rets.items(), key=lambda x: x[1], reverse=True)
        n = len(sorted_ind)
        out = {}
        for i, (ind, ret) in enumerate(sorted_ind):
            # rank 0 = 最强 -> RPS = 100；rank n-1 = 最弱 -> RPS ≈ 0
            out[ind] = (1.0 - float(i) / float(n)) * 100.0
        return out
