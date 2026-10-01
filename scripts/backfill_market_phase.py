# coding: utf-8
"""backfill_market_phase.py — 市场情绪周期 6 阶段全历史回填 + 锚点验证。

数据: D:/astock/daily/stock_daily.parquet (tushare, up_limit 精确涨停价)
      + data/etf_daily/510300_SH.csv (HS300 ETF, 供 index_pct)
输出: research_study/market_phase_backfill/daily_phase.csv + 控制台验证报告

验证锚点(与 TSP market-phase.md 声称一致):
  A1: 2024-09-24 → 10-08 应出现 启动→主升→高潮 (九二四行情)
  A2: 2024-01/02 微盘流动性危机 应为 退潮/冰点 (依赖弱档否决)
  A3: 2024-10-08 / 2024-10-31 附近出现 climax (17 板 ST 重组潮)
统计: 修复段占比(TSP ~74%)、平均段长(TSP ~9.7 天)、各阶段分布
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from factors.market_phase import (  # noqa: E402
    PHASE_LABELS,
    build_daily_metrics,
    add_limit_flags,
    classify_phase_series,
    classify_state_series,
    segment_stats,
)

ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
HS300_ETF = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "etf_daily", "510300_SH.csv")
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "research_study", "market_phase_backfill")
REPORT_START = "2020-08-24"  # 创业板 20cm 生效日, TSP 标定窗口起点


def calibrate(rep):
    """按 TSP 标定配方在 astock 数据上重算阈值(打印 Python 常量块)。

    TSP 配方(值 → 分位依据):
      CLIMAX_GE2=2*p90(ge2)  CLIMAX_FB=2.5*p90(fb)
      RALLY: p60(h)/p60(g2)/p60(promo); ALT: p85(promo), 0.8*p60(g2), 5/7*p60(h)
      EBB: p20(promo); strict=0.867*p20; seal=p10(seal); recent_ge2=0.48*p90(g2); recent_h=0.857*p60(h)
      IGNITE: g2>=p30(g2), promo>=p55, h>=p40(h), soft>=p50(promo)
      ICE: p10(h)/p10(g2)/p10(fb)
    """
    q = rep[["height", "first_board", "ge2", "promo_rate", "seal_rate"]].quantile(
        [0.10, 0.20, 0.30, 0.40, 0.50, 0.55, 0.60, 0.85, 0.90])
    h, fb, g2, pr, sr = (q[c] for c in ["height", "first_board", "ge2", "promo_rate", "seal_rate"])
    lines = [
        "# astock 重标定 (窗口 %s ~ %s, %d 交易日, 配方同 TSP)" % (
            rep.index.min().date(), rep.index.max().date(), len(rep)),
        "CLIMAX_GE2 = %d            # 2*p90(ge2)" % round(2 * g2[0.90]),
        "CLIMAX_FIRST_BOARD = %d   # 2.5*p90(fb)" % round(2.5 * fb[0.90]),
        "RALLY_HEIGHT = %d           # p60(height)" % round(h[0.60]),
        "RALLY_GE2 = %d             # p60(ge2)" % round(g2[0.60]),
        "RALLY_PROMO = %.3f         # p60(promo)" % pr[0.60],
        "RALLY_PROMO_ALT = %.3f     # p85(promo)" % pr[0.85],
        "RALLY_GE2_ALT = %d          # 0.8*p60(ge2)" % round(0.8 * g2[0.60]),
        "RALLY_HEIGHT_ALT = %d       # 5/7*p60(height)" % round(5.0 / 7.0 * h[0.60]),
        "EBB_PROMO = %.3f           # p20(promo)" % pr[0.20],
        "EBB_PROMO_STRICT = %.3f    # 0.867*p20(promo)" % (pr[0.20] * 0.867),
        "EBB_SEAL = %.3f            # p10(seal_rate)" % sr[0.10],
        "EBB_RECENT_GE2 = %d        # 0.48*p90(ge2)" % round(0.48 * g2[0.90]),
        "EBB_RECENT_HEIGHT = %d     # 0.857*p60(height)" % round(0.857 * h[0.60]),
        "IGNITE_GE2 = %d            # p30(ge2)" % round(g2[0.30]),
        "IGNITE_PROMO = %.3f        # p55(promo)" % pr[0.55],
        "IGNITE_HEIGHT = %d         # p40(height)" % round(h[0.40]),
        "IGNITE_PROMO_SOFT = %.3f   # p50(promo)" % pr[0.50],
        "ICE_HEIGHT = %d            # p10(height)" % round(h[0.10]),
        "ICE_GE2 = %d               # p10(ge2)" % round(g2[0.10]),
        "ICE_FIRST_BOARD = %d      # p10(first_board)" % round(fb[0.10]),
    ]
    return "\n".join(lines)


def main():
    t0 = time.time()
    print("[1/5] 读取 astock 日频 ...")
    df = pd.read_parquet(ASTOCK_DAILY,
                         columns=["high", "close", "pre_close", "up_limit", "adj_factor"])
    df = df.reset_index()  # trade_date, ts_code 变回普通列
    df["ts_code"] = df["ts_code"].astype("category")
    print("      rows=%d, %s ~ %s" % (len(df), df["trade_date"].min().date(), df["trade_date"].max().date()))

    print("[2/5] 涨停标志 + 连板数 + change_pct + above_ma20 ...")
    df["change_pct"] = df["close"] / df["pre_close"] - 1.0
    df = df.sort_values(["ts_code", "trade_date"], kind="mergesort")
    adj_close = df["close"] * df["adj_factor"]
    ma20 = adj_close.groupby(df["ts_code"], observed=True).transform(
        lambda s: s.rolling(20, min_periods=20).mean())
    df["above_ma20"] = (adj_close > ma20).astype(bool)
    df = df.drop(columns=["pre_close", "adj_factor"])
    flagged = add_limit_flags(df)
    print("      flagged rows=%d, 涨停行=%d, took %.0fs" % (
        len(flagged), int(flagged["limit_up"].sum()), time.time() - t0))

    print("[3/5] HS300 ETF index_pct ...")
    etf = pd.read_csv(HS300_ETF)
    etf["date"] = pd.to_datetime(etf["date"])
    etf = etf.sort_values("date")
    idx_pct = (etf["close"].pct_change()).values
    index_pct = dict(zip(etf["date"], np.nan_to_num(idx_pct)))

    print("[4/5] 日度聚合 + 5 档 state + 6 阶段 ...")
    daily = build_daily_metrics(flagged, index_pct=index_pct)
    states, scores = classify_state_series(daily)
    daily["state"] = states
    daily["score"] = scores
    daily = classify_phase_series(daily, states=states)
    daily["phase_cn"] = daily["phase"].map(PHASE_LABELS)
    print("      days=%d, took %.0fs" % (len(daily), time.time() - t0))

    print("[5/5] 验证报告 ...")
    rep = daily.loc[REPORT_START:]
    print("\n===== 阶段分布 (%s ~ %s, %d 交易日) =====" % (
        rep.index.min().date(), rep.index.max().date(), len(rep)))
    dist = rep["phase"].value_counts(normalize=True).sort_values(ascending=False)
    for ph, share in dist.items():
        print("  %-7s %-2s %6.1f%%" % (ph, PHASE_LABELS.get(ph, ""), share * 100))

    print("\n===== 段长统计 (TSP 参考: 平均 9.7 天, 修复占比 ~74%) =====")
    seg = segment_stats(rep["phase"])
    seg["share%"] = rep["phase"].value_counts(normalize=True)[seg.index] * 100
    print(seg.to_string(float_format=lambda v: "%.2f" % v))

    print("\n===== 驱动量分位数对照 (raw, 2020-08-24+; TSP 标定: fb p10=24/p90=88, ge2 p10=6/p60=15/p90=25, h p60=7) =====")
    qs = rep[["height", "first_board", "ge2", "promo_rate", "seal_rate"]].quantile([0.1, 0.2, 0.6, 0.85, 0.9])
    print(qs.to_string(float_format=lambda v: "%.3f" % v))

    if "--calibrate" in sys.argv:
        print("\n===== astock 重标定建议 =====")
        print(calibrate(rep))
        return

    print("\n===== 锚点 A1: 2024-09-24 → 10-31 (九二四行情, 期望 启动→主升→高潮) =====")
    a1 = daily.loc["2024-09-20":"2024-10-31",
                   ["height", "first_board", "ge2", "promo_rate", "seal_rate", "state", "phase", "phase_cn"]]
    print(a1.to_string(float_format=lambda v: "%.3f" % v))

    print("\n===== 锚点 A2: 2024-01-02 → 02-08 (微盘流动性危机, 期望 退潮/冰点) =====")
    a2 = daily.loc["2024-01-02":"2024-02-08",
                   ["height", "first_board", "ge2", "promo_rate", "seal_rate", "state", "phase", "phase_cn"]]
    print(a2.to_string(float_format=lambda v: "%.3f" % v))

    print("\n===== 锚点 A3: 全期高度 Top5 (期望 2024-10 ST 重组潮 17 板在列) =====")
    top = rep.nlargest(5, "height")[["height", "first_board", "ge2", "phase", "phase_cn"]]
    print(top.to_string())

    print("\n===== 对照: 同期无否决版本 (states=None) 锚点差异 =====")
    daily_nv = classify_phase_series(daily, states=None)
    for lo, hi, name in [("2024-09-20", "2024-10-31", "A1"),
                         ("2024-01-02", "2024-02-08", "A2")]:
        diff = (daily_nv.loc[lo:hi, "phase"] != daily.loc[lo:hi, "phase"]).sum()
        print("  %s 区间否决改动天数: %d" % (name, diff))

    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, "daily_phase.csv")
    daily.to_csv(out_csv, encoding="utf-8-sig")
    print("\n已保存: %s (rows=%d, took %.0fs)" % (out_csv, len(daily), time.time() - t0))


if __name__ == "__main__":
    main()
