# coding=utf-8
"""529 池内趋势排序实证：从 529 选出的股票里按趋势强度排序取前 5/前30%，对比全池。

回答核心问题：529 池内"趋势最强"排序是否带来更高的命中率/收益？
若排序无效，则"取前5只"方案立不住（持仓集中=单票运气，RPS教训）。
"""
import sys
import time
import argparse

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_12_RPS主升浪/research")

import numpy as np
import pandas as pd

from huangshi_formula_scan import (load_stock_panel, load_index, compute_indicators,
                                   pre_signal_529, compute_cost_candidates, signal_529,
                                   compute_stats)


def rank_metrics(ind):
    """定义 6 个候选"趋势强度"排序指标（全部 PIT 安全）。"""
    out = pd.DataFrame(index=ind.index)
    close = ind["close"]
    # 1. 20日动量
    out["mom20"] = close / close.groupby(level="ts_code").shift(20) - 1.0
    # 2. 60日动量
    out["mom60"] = close / close.groupby(level="ts_code").shift(60) - 1.0
    # 3. RPS20（20日涨幅截面百分位，0-100）
    mom20_wide = out["mom20"].unstack("ts_code")
    out["rps20"] = mom20_wide.rank(axis=1, pct=True).stack() * 100.0
    # 4. MA5 角度
    out["angle5"] = ind["angle5"]
    # 5. 突破幅度（相对60日高点）
    hhv60 = ind["hhv_h_60"].groupby(level="ts_code").shift(1)
    out["brk_pct"] = (close / hhv60 - 1.0) * 100.0
    # 6. 距60日新高距离（越接近新高越强）
    out["near_high"] = close / ind["hhv_h_60"]
    # 7. ATR%（20日真实波幅/价格，低波=好）——QuantLab 已验证强因子
    tr = pd.concat([ind["high"] - ind["low"],
                    (ind["high"] - close.groupby(level="ts_code").shift(1)).abs(),
                    (ind["low"] - close.groupby(level="ts_code").shift(1)).abs()], axis=1).max(axis=1)
    out["atr_pct"] = tr.groupby(level="ts_code").transform(lambda x: x.rolling(20, min_periods=20).mean()) / close * 100.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2025-12-31")
    args = ap.parse_args()

    print("=== 529 池内趋势排序实证: %s ~ %s ===" % (args.start, args.end))
    df = load_stock_panel(args.start, args.end)
    idx = load_index(args.start, args.end)
    ind = compute_indicators(df)

    # 大盘（DeepSeek 等不用，此处只需 close 收益）
    dates_all = ind.index.get_level_values("trade_date").unique()
    idx_close = idx.reindex(dates_all).ffill()

    # 529 信号
    pre529 = pre_signal_529(ind)
    print("529 非筹码候选: %d" % int(pre529.sum()))
    from huangshi_formula_scan import compute_cost_candidates as ccc
    cost5, cost95 = ccc(df, pre529)
    sig = signal_529(ind, cost5, cost95)
    print("529 信号: %d" % int(sig.sum()))

    # 排序指标
    rm = rank_metrics(ind)
    sig_df = sig[sig]
    rm_sig = rm.loc[sig_df.index]
    rm_sig = rm_sig.rename_axis(index=["date", "code"])
    fwd = compute_stats(sig, df, idx_close, fwd_days_list=(5, 10, 20, 60))
    merged = fwd.join(rm_sig)

    print("\n=== 全池基线（n=%d）===" % len(merged))
    for n in [5, 10, 20, 60]:
        v = merged["fwd%d" % n].dropna()
        print("  fwd%-3d 均值%+6.2f%% 命中率%5.1f%% (n=%d)" % (n, v.mean(), (v > 0).mean() * 100, len(v)))

    print("\n=== 按各排序指标取每日前5只（再对比前30%/全池）===")
    asc_cols = {"atr_pct"}  # 低波=好，升序排前
    for col in ["mom20", "mom60", "rps20", "angle5", "brk_pct", "near_high", "atr_pct"]:
        rank_dir = "ascending" if col in asc_cols else "descending"
        merged["_rank"] = merged.groupby(level=0)[col].rank(ascending=(rank_dir == "ascending"))
        top5 = merged[merged["_rank"] <= 5]
        top30 = merged[merged["_rank"] <= merged.groupby(level=0)[col].transform("count") * 0.30]
        print("\n--- 排序指标: %s (%s) ---" % (col, rank_dir))
        for label, sub in [("全池", merged), ("前5只", top5), ("前30%", top30)]:
            v20 = sub["fwd20"].dropna()
            v60 = sub["fwd60"].dropna()
            if len(v20) == 0:
                continue
            print("  %-5s n=%5d | fwd20 均值%+6.2f%% 命中%5.1f%% 大涨≥20%% %4.1f%% | fwd60 均值%+6.2f%% 命中%5.1f%%"
                  % (label, len(sub), v20.mean(), (v20 > 0).mean() * 100,
                     (v20 >= 20).mean() * 100, v60.mean(), (v60 > 0).mean() * 100))

    print("\n=== 反向验证：趋势最弱批次（若 alpha 来自分散而非最强，弱批次应≥全池）===")
    for col in ["mom20", "rps20", "near_high"]:
        merged["_rank"] = merged.groupby(level=0)[col].rank(ascending=False)
        cnt = merged.groupby(level=0)[col].transform("count")
        bottom5 = merged[merged["_rank"] > cnt - 5]
        bottom30 = merged[merged["_rank"] > cnt * 0.70]
        v20t = merged["fwd20"].dropna()
        v20b5 = bottom5["fwd20"].dropna()
        v20b30 = bottom30["fwd20"].dropna()
        print("  %-9s 前5只 %+6.2f%% / 全池 %+6.2f%% / 最弱5只 %+6.2f%% / 最弱30%% %+6.2f%%"
              % (col, top5["fwd20"].dropna().mean() if col != "near_high" else merged[merged["_rank"] <= 5]["fwd20"].dropna().mean(),
                 v20t.mean(), v20b5.mean(), v20b30.mean()))

    # 年度稳定性：最优排序指标的前5只逐年
    print("\n=== 年度明细（各排序取前5的 fwd20 均值）===")
    rows = []
    for col in ["mom20", "mom60", "rps20", "angle5", "brk_pct", "near_high"]:
        merged["_rank"] = merged.groupby(level=0)[col].rank(ascending=False)
        top5 = merged[merged["_rank"] <= 5]
        top5["year"] = top5.index.get_level_values(0).year
        y = top5.groupby("year")["fwd20"].mean()
        rows.append(pd.Series(y.round(2), name=col))
    yearly = pd.concat(rows, axis=1)
    print(yearly.to_string())


if __name__ == "__main__":
    main()