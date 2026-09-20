# coding=utf-8
"""诊断：为什么生产 ATR 池上 amt_ratio 域内排除零影响？

逐季复现 backtest/engine + atr_lowvol_amt_exclude 的 eligible 语义，
对每个季度调仓日统计：
  - eligible 池大小、amt_ratio top20% 剔除数
  - 被选 8 只（ATR% 最低）落入剔除集的数量（重叠数）
  - 被选 8 只的 amt_ratio 分位位置（0=最低, 1=最高）
同时对比简化版 ablation 的小盘候选池（circ_mv<30亿 + PE>0 + PB>0）口径。

用法:
  D:\\Python311\\python.exe research_factor_mining/diag_amt_overlap.py
"""
import json, os, sys, time
import numpy as np
import pandas as pd

PROJECT_ROOT = "D:/QuantLab"
sys.path.insert(0, PROJECT_ROOT)

from data.astock_reader import AstockParquetReader
from data.universe import load_universe
from factors.atr import atr_pct
from factors.roe import get_roe_asof
from strategy.schedule import is_rebalance_day

DAILY_PATH = r"D:/astock/daily/stock_daily.parquet"
HERE = os.path.dirname(os.path.abspath(__file__))
START, END = "2019-01-01", "2026-09-11"
WARMUP = "2017-08-01"
N_HOLD, ATR_WIN, ATR_MAX = 8, 14, 0.06
TO_MIN, TO_MAX, MAX_PRICE = 1.0, 8.0, 50.0
EXCL_PCT = 0.20


def build_eligible(market_window, codes, current_date, smallcap_pool=False):
    """复现 atr_lowvol_amt_exclude 的 eligible 构建；返回 [(code, atr_pct, amt_ratio)]"""
    out = []
    for c in codes:
        df = market_window.get(c)
        if df is None or len(df) < 252:
            continue
        last = df.iloc[-1]
        if MAX_PRICE > 0:
            af = last.get("adj_factor")
            if af is not None and float(af) > 0:
                if float(last.get("close", 0)) / float(af) >= MAX_PRICE:
                    continue
        to = last.get("turnover_rate")
        if to is None or not (TO_MIN <= float(to) <= TO_MAX):
            continue
        if bool(last.get("is_st", False)):
            continue
        if smallcap_pool:
            # ablation get_candidates 口径：流通市值<30亿(万元<300000) + PE>0 + PB>0
            mv = last.get("circ_mv")
            pe = last.get("pe_ttm")
            pb = last.get("pb")
            if mv is None or not (0 < float(mv) < 300000):
                continue
            if pe is None or float(pe) <= 0 or pb is None or float(pb) <= 0:
                continue
        ap = atr_pct(df, ATR_WIN)
        if ap <= 0 or ap > ATR_MAX:
            continue
        roe = get_roe_asof(c, current_date)
        if roe is None or roe <= 0:
            continue
        close = df["close"].astype(float).values
        if len(close) >= 252:
            ret_12_1 = (close[-21] / close[-252] - 1.0) if close[-252] > 0 else 0.0
        else:
            ret_12_1 = 0.0
        if ret_12_1 <= 0:
            continue
        ratio = np.nan
        if "amount" in df.columns and "circ_mv" in df.columns:
            amt = df["amount"].astype(float).tail(20)
            mv = float(df["circ_mv"].astype(float).iloc[-1])
            if mv > 0 and not amt.dropna().empty:
                ratio = float(amt.mean()) / mv
        out.append((c, ap, ratio))
    return out


def diag_pool(eligible, label, rows, d):
    if len(eligible) <= N_HOLD:
        return
    eligible_sorted = sorted(eligible, key=lambda r: r[1])
    selected = eligible_sorted[:N_HOLD]
    ratios = np.array([r[2] if r[2] == r[2] else np.nan for r in eligible])
    valid = ~np.isnan(ratios)
    n_valid = int(valid.sum())
    if n_valid < N_HOLD + 2:
        rows.append({"date": d, "pool": label, "eligible": len(eligible),
                     "n_valid_ratio": n_valid, "excluded": 0, "overlap": -1,
                     "sel_pct_max": None, "sel_pct_med": None})
        return
    # ablation 语义阈值：非缺失值的 80% 分位
    thr = float(np.quantile(ratios[valid], 1.0 - EXCL_PCT))
    excluded = {r[0] for r in eligible if r[2] == r[2] and r[2] > thr}
    # 被选 8 只的 amt_ratio 在域内的分位位置
    sel_r = np.array([r[2] if r[2] == r[2] else np.nan for r in selected])
    ranks = []
    for v in sel_r:
        if v != v:
            continue
        ranks.append(float((ratios[valid] <= v).mean()))
    overlap = sum(1 for r in selected if r[0] in excluded)
    rows.append({"date": d, "pool": label, "eligible": len(eligible),
                 "n_valid_ratio": n_valid, "excluded": len(excluded),
                 "overlap": overlap,
                 "sel_pct_max": max(ranks) if ranks else None,
                 "sel_pct_med": float(np.median(ranks)) if ranks else None})


def main():
    t0 = time.time()
    print("start", time.strftime("%H:%M:%S"), flush=True)
    universe = load_universe("D:/QuantLab/data/universe_all_a.csv")["codes"]
    reader = AstockParquetReader(DAILY_PATH, adjustment="hfq")
    calendar = reader.trading_calendar(START, END)
    rebal_dates = [d for d in calendar if is_rebalance_day(d, "quarterly", calendar)]
    print("rebalance dates: %d %s ... %s" % (len(rebal_dates), rebal_dates[0], rebal_dates[-1]), flush=True)

    full = reader.load_window(universe, WARMUP, END)
    reader.close()
    print("window loaded: %d codes, %.0fs" % (len(full), time.time() - t0), flush=True)

    # 预计算每个 code 的 date 数组，季切 iloc 视图
    date_arrs = {c: df["date"].values for c, df in full.items()}

    rows = []
    for d in rebal_dates:
        t1 = time.time()
        win = {}
        for c, df in full.items():
            k = np.searchsorted(date_arrs[c], d, side="right")
            if k >= 252:
                win[c] = df.iloc[:k]
        for pool, sc in [("prod_allcap", False), ("ablation_smallcap", True)]:
            elig = build_eligible(win, list(win.keys()), d, smallcap_pool=sc)
            diag_pool(elig, pool, rows, d)
        r0 = [r for r in rows if r["date"] == d]
        print("%s prod: elig=%s overlap=%s | smallcap: elig=%s overlap=%s (%.0fs)"
              % (d,
                 [r["eligible"] for r in r0 if r["pool"] == "prod_allcap"],
                 [r["overlap"] for r in r0 if r["pool"] == "prod_allcap"],
                 [r["eligible"] for r in r0 if r["pool"] == "ablation_smallcap"],
                 [r["overlap"] for r in r0 if r["pool"] == "ablation_smallcap"],
                 time.time() - t1), flush=True)

    out = os.path.join(HERE, "diag_amt_overlap_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f,
                  ensure_ascii=False, indent=1)
    print("saved:", out, "| total %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
