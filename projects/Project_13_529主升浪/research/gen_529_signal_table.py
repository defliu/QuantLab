# coding: utf-8
"""生成 529 信号表（每日 ATR 低波 top16）供回测引擎查表。

PIT 安全：信号只用当日及之前数据（收盘后确认），引擎 next_open 成交。
输出：JSON dict {date: [codes]}（date 为 "YYYY-MM-DD"，codes 已按 ATR 升序 top16）。
      n_hold=8/12/16 时策略只取前 n 只，一个表通吃。
"""
import json
import sys
import time

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_12_RPS主升浪/research")

import numpy as np
import pandas as pd

from huangshi_formula_scan import (load_stock_panel, compute_indicators,
                                   pre_signal_529, compute_cost_candidates,
                                   signal_529)

OUT_PATH = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16.json"
OUT_MARKET = "D:/QuantLab/projects/Project_13_529主升浪/research/market_ma200.json"


def atr_pct_series(ind, win=20):
    """20 日 ATR%（真实波幅均值/收盘价），按股票 group 计算。"""
    close = ind["close"]
    high = ind["high"]
    low = ind["low"]
    pc = close.groupby(level="ts_code").shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    atr = tr.groupby(level="ts_code").transform(
        lambda x: x.rolling(win, min_periods=win).mean())
    return atr / close * 100.0


def main():
    start, end = "2018-06-01", "2026-07-31"
    print("=== 生成 529 信号表 %s ~ %s ===" % (start, end))
    t0 = time.time()
    df = load_stock_panel(start, end)
    print("[load] %.1fs" % (time.time() - t0))
    ind = compute_indicators(df)
    print("[ind] %.1fs" % (time.time() - t0))

    pre529 = pre_signal_529(ind)
    cost5, cost95 = compute_cost_candidates(df, pre529)
    sig = signal_529(ind, cost5, cost95)
    print("529 信号总数: %d" % int(sig.sum()))

    atr = atr_pct_series(ind)

    sig_rows = sig[sig]
    atr_sig = atr.loc[sig_rows.index]

    table = {}
    g = sig_rows.groupby(level="trade_date")
    for date, idx in g.groups.items():
        sub = atr_sig.loc[idx].dropna()
        if len(sub) == 0:
            continue
        top16 = sub.sort_values().index.get_level_values("ts_code").tolist()[:16]
        table[str(date)[:10]] = top16

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False)
    n_days = len(table)
    n_sigs = sum(len(v) for v in table.values())
    print("信号表: %d 天, 共 %d 只次, 日均 %.1f (top16)"
          % (n_days, n_sigs, n_sigs / max(1, n_days)))
    print("样本日 %s: %s" % (list(table.keys())[0], table[list(table.keys())[0]]))

    # 市场等权收盘指数 + MA200 状态（PIT 安全：只用当日及之前数据）
    close_wide = ind["close"].unstack("ts_code")
    daily_mean = close_wide.mean(axis=1, skipna=True)  # 每日全市场等权
    ma200 = daily_mean.rolling(200, min_periods=200).mean()
    market_ok = {}
    for d, v in daily_mean.items():
        m = ma200.get(d)
        ds = str(d)[:10]
        market_ok[ds] = bool(v > m) if m == m else True
    with open(OUT_MARKET, "w", encoding="utf-8") as f:
        json.dump(market_ok, f, ensure_ascii=False)
    print("市场MA200表: %d 天 (out=%s)" % (len(market_ok), OUT_MARKET))
    print("输出: %s" % OUT_PATH)
    print("[total] %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()