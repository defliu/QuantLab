# coding: utf-8
"""gpsj 备用数据源交叉验证 —— 箱体突破模块（2020 单年）。

目的：验证信号扫描结果不依赖数据口径（AGENTS.md 硬规则：
"每个新策略/因子回测定稿后，必须用备用数据源随机抽取 1 个自然年做同区间对比"）。

方法：取同一批随机股票，分别用 astock 与 gpsj 数据源跑 2020 年
箱体突破信号 + 前向收益，对比信号数与 fwd 统计量。
"""
import random
import sys

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_18_买卖点信号工具/research")

import numpy as np
import pandas as pd

from buypoint_signals import (
    ASTOCK_DAILY,
    INDEX_CODE,
    INDEX_DB,
    _DEFAULT_CFG,
    compute_indicators,
    compute_stats,
    gshift,
    load_index,
    load_stock_panel,
    signal_box_breakout,
    summarize,
)


def panel_from_gpsj(reader, codes, start, end):
    """用 GpsjDuckDBReader 构建与 buypoint_signals 相同格式的面板。"""
    frames = []
    for code, df in reader.load_window(codes, start, end).items():
        d = df[["date", "open", "high", "low", "close", "vol", "amount",
                "turnover_rate", "circ_mv", "is_st", "adj_factor"]].copy()
        d["ts_code"] = code
        frames.append(d)
    panel = pd.concat(frames, ignore_index=True)
    panel["trade_date"] = pd.to_datetime(panel["date"])
    panel = panel.drop(columns=["date"])
    # 归一化 is_st：gpsj 的 "ST" 列语义与 tushare/astock 相反，且 numpy 标量判断易出错。
    # 交叉验证目的 = 隔离"价格/量数据源"差异对信号的影响；is_st 只是过滤条件非信号输入，
    # 因此这里不依赖 gpsj 的 ST 列，统一由调用方用 astock 的 is_st 对齐（见 main）。
    panel["is_st"] = 0
    panel["circ_mv"] = panel["circ_mv"].astype(float)
    panel["turnover_rate"] = panel["turnover_rate"].astype(float)
    # hfq 复权
    adj = panel["adj_factor"].astype(float)
    for c in ["open", "high", "low", "close"]:
        panel[c] = panel[c].astype(float) * adj
    panel = panel.sort_values(["ts_code", "trade_date"])
    panel = panel.set_index(["trade_date", "ts_code"])
    return panel


def run_signal(panel, idx_close, cfg):
    ind = compute_indicators(panel)
    sig = signal_box_breakout(ind, cfg)
    stats = compute_stats(sig, panel, idx_close)
    return summarize("箱体突破(2020)", stats)


def main():
    random.seed(42)
    start, end = "2020-01-01", "2020-12-31"

    # 同一批随机股票（astock 与 gpsj 均存在）
    all_codes = sorted(
        load_stock_panel(start, end).index.get_level_values("ts_code").unique().tolist())
    codes = random.sample(all_codes, min(600, len(all_codes)))
    print("[codes] 随机抽取 %d 只：%s ..." % (len(codes), codes[:3]))

    idx = load_index(start, end)

    print("\n### astock 数据源 ###")
    panel_a = load_stock_panel(start, end, codes=codes)
    idx_close_a = idx.reindex(panel_a.index.get_level_values("trade_date").unique()).ffill()
    ra = run_signal(panel_a, idx_close_a, dict(_DEFAULT_CFG))

    print("\n### gpsj 数据源 ###")
    from data.gpsj_reader import GpsjDuckDBReader
    reader = GpsjDuckDBReader(adjustment="raw")
    panel_g = panel_from_gpsj(reader, codes, start, end)
    # 用 astock 的 is_st 对齐 gpsj 面板（is_st 只是过滤条件，非信号输入；
    # gpsj 的 ST 列语义与 tushare 相反，见 AGENTS.md gpsj_reader 口径说明）
    panel_g["is_st"] = panel_a["is_st"].reindex(panel_g.index).fillna(0)
    idx_close_g = idx.reindex(panel_g.index.get_level_values("trade_date").unique()).ffill()
    rg = run_signal(panel_g, idx_close_g, dict(_DEFAULT_CFG))

    print("\n" + "=" * 72)
    print("对比（astock vs gpsj）：")
    if ra and rg:
        for k in ["n_signals", "fwd5_mean", "fwd20_mean", "fwd20_hit"]:
            print("  %-12s astock=%-10s gpsj=%s"
                  % (k, ra.get(k), rg.get(k)))
    reader.close()


if __name__ == "__main__":
    main()
