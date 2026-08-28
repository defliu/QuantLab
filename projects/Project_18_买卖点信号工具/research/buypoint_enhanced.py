# coding: utf-8
"""买卖点信号增强版扫描 —— 「信号 + 大盘门控 + 板块过滤」。

基于 B 方案证伪结论（4 类买点裸信号无稳定 edge）的增强评估：
叠加两层过滤后，命中率/超额是否改善到"可用"水平。

变体（对每个买点模块分别评估）：
  base        裸信号（对照）
  gate        信号 + 大盘门控（严格：INDEXC > MA20 > MA60）
  sector      信号 + 板块过滤（行业 RPS 前 top_n）
  gate+sector 信号 + 大盘门控 + 板块过滤

实现为向量化（复用 buypoint_signals 的指标库/信号/统计）：
  - 大盘门控：指数收盘 > MA20 > MA60（与黄氏扫描一致，P10/P13 已验证门控有效）
  - 板块过滤：个股所属行业(申万, stock_basic.parquet) 的等权 20 日涨幅在当日全行业百分位
    （RPS>=threshold 或前 top_n），与 rps_momentum 的板块 RPS 同口径但向量化

用法：
  python research/buypoint_enhanced.py [--start 2019-01-01] [--end 2025-12-31] [--out xxx.json]
"""
import argparse
import json
import sys
import time

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_18_买卖点信号工具/research")

import numpy as np
import pandas as pd

from buypoint_signals import (
    _DEFAULT_CFG,
    compute_indicators,
    compute_stats,
    gshift,
    load_index,
    load_stock_panel,
    signal_box_breakout,
    signal_cup_handle,
    signal_second_launch,
    signal_trend_pullback,
    summarize,
)

SIGNALS = {
    "1_箱体突破": signal_box_breakout,
    "2_杯柄图形": signal_cup_handle,
    "3_趋势低吸": signal_trend_pullback,
    "4_二次启动": signal_second_launch,
}

STOCK_BASIC = "E:/astock/basic/stock_basic.parquet"

# 增强参数
GATE_MODE = "strict"          # strict=INDEXC>MA20>MA60；ma60=INDEXC>MA60
SECTOR_WINDOW = 20            # 行业 RPS 窗口（交易日）
SECTOR_RPS_MIN = 80.0         # 行业 RPS 阈值（0-100）
SECTOR_TOP_N = 5              # 行业 RPS 前 N
SECTOR_MIN_MEMBERS = 3        # 行业最少有效成员数


# ---------------------------------------------------------------------------
# 行业映射
# ---------------------------------------------------------------------------
def load_industry_map():
    import pyarrow.parquet as pq
    t = pq.read_table(STOCK_BASIC, columns=["ts_code", "industry"])
    df = t.to_pandas()
    df = df[df["industry"].notna() & (df["industry"].str.strip() != "")]
    return dict(zip(df["ts_code"], df["industry"]))


# ---------------------------------------------------------------------------
# 大盘门控（按日期映射到面板）
# ---------------------------------------------------------------------------
def compute_market_gate_series(idx_close, mode=GATE_MODE):
    """idx_close: index close Series indexed by date；返回 {date: bool}。"""
    ma20 = idx_close.rolling(20, min_periods=20).mean()
    ma60 = idx_close.rolling(60, min_periods=60).mean()
    if mode == "strict":
        return ((idx_close > ma20) & (ma20 > ma60))
    if mode == "ma60":
        return (idx_close > ma60)
    return (idx_close > ma20)


# ---------------------------------------------------------------------------
# 板块过滤（向量化行业 RPS 百分位 -> 面板）
# ---------------------------------------------------------------------------
def compute_sector_rps_panel(panel, industry_map, window=SECTOR_WINDOW,
                             min_members=SECTOR_MIN_MEMBERS):
    """返回与 panel 同 index 的行业 RPS 百分位 Series（0-100，未知行业为 NaN）。"""
    code2ind = pd.Series(industry_map, dtype=object)
    stk_ind = panel.index.get_level_values("ts_code").map(code2ind)

    close = panel["close"]
    ret = close / gshift(close, window) - 1.0  # 个股 trailing 20 日涨幅

    tmp = pd.DataFrame({
        "date": ret.index.get_level_values("trade_date"),
        "industry": stk_ind.values,
        "ret": ret.values,
    }).dropna(subset=["ret", "industry"])

    cnt = tmp.groupby(["date", "industry"]).size()
    valid = cnt[cnt >= min_members].index
    grp = tmp.set_index(["date", "industry"]).loc[valid]
    grp = grp.groupby(["date", "industry"])["ret"].mean()

    rps = grp.groupby("date").rank(pct=True) * 100.0  # index=(date, industry)

    stk_idx = pd.MultiIndex.from_arrays(
        [panel.index.get_level_values("trade_date"), stk_ind.values],
        names=["date", "industry"])
    out = rps.reindex(stk_idx)
    out.index = panel.index
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_period(start, end, cfg=None):
    t0 = time.time()
    cfg = dict(_DEFAULT_CFG)
    cfg.update(cfg or {})
    print("\n=== 增强版扫描: %s ~ %s ===" % (start, end))

    df = load_stock_panel(start, end)
    idx = load_index(start, end)
    idx_close = idx.reindex(df.index.get_level_values("trade_date").unique()).ffill()
    gate = compute_market_gate_series(idx_close, GATE_MODE)
    gate_map = {d: bool(v) for d, v in gate.items()}

    ind = compute_indicators(df)
    panel_gate = pd.Series(
        [gate_map.get(d, False) for d in ind.index.get_level_values("trade_date")],
        index=ind.index)

    industry_map = load_industry_map()
    sector_rps = compute_sector_rps_panel(df, industry_map)
    sector_top = sector_rps >= SECTOR_RPS_MIN

    sigs = {name: fn(ind, cfg) for name, fn in SIGNALS.items()}

    variants = {
        "base": lambda s: s,
        "gate": lambda s: s & panel_gate,
        "sector": lambda s: s & sector_top,
        "gate+sector": lambda s: s & panel_gate & sector_top,
    }

    results = []
    for sname, sig in sigs.items():
        print("\n########## %s ##########" % sname)
        for vname, fn in variants.items():
            sv = fn(sig)
            stats_df = compute_stats(sv, df, idx_close)
            r = summarize("%s[%s]" % (sname, vname), stats_df)
            if r:
                r["module"] = sname
                r["variant"] = vname
                results.append(r)
    print("\n[period] %.0fs" % (time.time() - t0))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()
    results = run_period(args.start, args.end)
    if args.out and results:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("\n[out] %s" % args.out)
    return results


if __name__ == "__main__":
    main()
