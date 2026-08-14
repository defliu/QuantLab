# coding: utf-8
"""板块 RPS 模块单元验证。

验证：
  1. SectorRPS 能从 stock_basic.parquet 加载行业归属
  2. compute_sector_returns 计算行业等权涨幅
  3. compute_sector_rps 计算行业百分位
  4. code_to_industry 正确返回行业
"""
import os
import sys

QL = "D:/QuantLab"
if QL not in sys.path:
    sys.path.insert(0, QL)

import pandas as pd
import numpy as np

from strategy.sector_rps import SectorRPS


def make_df(n=300, trend=0.002, vol_base=1e7):
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(42)
    rets = rng.normal(trend, 0.02, n)
    close = 10 * np.cumprod(1 + rets)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "vol": np.abs(rng.normal(vol_base, vol_base * 0.2, n)),
        "is_st": 0.0,
    })
    return df


def test_load_industry():
    sr = SectorRPS()
    sr.load()
    assert sr._industry_map is not None and len(sr._industry_map) > 100, "行业映射未加载"
    # 平安银行应属于银行业
    ind = sr.code_to_industry("000001.SZ")
    assert ind is not None, "平安银行应有行业"
    print("[OK] SectorRPS.load: %d 只股票有行业, 平安银行 -> %s" % (len(sr._industry_map), ind))


def test_compute_sector_returns():
    sr = SectorRPS()
    sr.load()
    # 构造 3 只不同行业股票：银行涨、半导体大涨、地产跌
    # 用真实行业映射（不硬编码行业名，避免测试数据行业归属变化）
    market_window = {
        "000001.SZ": make_df(trend=0.002),   # 银行（涨）
        "688111.SH": make_df(trend=0.008),   # 某科技股（大涨）
        "000002.SZ": make_df(trend=-0.004),  # 地产（跌）
    }
    # 确认这三只都有行业且分属不同行业
    inds = {c: sr.code_to_industry(c) for c in market_window}
    print("    行业映射: %s" % inds)
    assert all(v is not None for v in inds.values()), "测试股票应有行业"
    assert len(set(inds.values())) == 3, "测试股票应分属 3 个不同行业"

    rets = sr.compute_sector_returns(market_window, 20)
    print("[OK] compute_sector_returns: %s" % {k: round(v, 4) for k, v in rets.items()})
    for ind in set(inds.values()):
        assert ind in rets, "应含行业 %s" % ind
    # 大涨股行业涨幅应 > 上涨股行业 > 下跌股行业
    ind_high = inds["688111.SH"]
    ind_mid = inds["000001.SZ"]
    ind_low = inds["000002.SZ"]
    assert rets[ind_high] > rets[ind_mid], "%s 应强于 %s" % (ind_high, ind_mid)
    assert rets[ind_mid] > rets[ind_low], "%s 应强于 %s" % (ind_mid, ind_low)


def test_compute_sector_rps():
    sr = SectorRPS()
    sr.load()
    market_window = {
        "000001.SZ": make_df(trend=0.002),
        "688111.SH": make_df(trend=0.008),
        "000002.SZ": make_df(trend=-0.004),
    }
    rps = sr.compute_sector_rps(market_window, 20)
    print("[OK] compute_sector_rps: %s" % {k: round(v, 1) for k, v in rps.items()})
    ind_high = sr.code_to_industry("688111.SH")
    ind_low = sr.code_to_industry("000002.SZ")
    assert ind_high in rps and ind_low in rps, "应含测试行业"
    # 大涨股行业 RPS 应为最高（100 或接近）
    assert rps[ind_high] == max(rps.values()), "%s RPS 应最高" % ind_high
    # 下跌股行业 RPS 应为最低（接近 0）
    assert rps[ind_low] == min(rps.values()), "%s RPS 应最低" % ind_low


if __name__ == "__main__":
    print("=== 板块 RPS 模块单元验证 ===\n")
    test_load_industry()
    test_compute_sector_returns()
    test_compute_sector_rps()
    print("\n=== 板块 RPS 全部单元验证通过 ===")
