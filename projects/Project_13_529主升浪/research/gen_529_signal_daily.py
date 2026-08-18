# coding: utf-8
"""P13 黄氏529 —— 每日信号增量生成（QMT 只读 CSV 数据源）。

与 gen_529_signal_table.py 全量口径逐字段一致（复用同一套公式函数，避免口径漂移）：
  突破60日高(3日窗) & small_chg_5d>=2 & MA5>10>20 & MA60走平(>=shift1)
  & angle5>=30 & 收阳 & 排除 pit&ji_la & 筹码 (cost95-cost5)/cost5<=25%
按 ATR%(20日) 升序取 top16，供 QMT 侧 T-1 信号决策。

PIT 安全：信号只用截至目标日的数据（收盘后确认），引擎/实盘次日开盘执行。
性能：全历史 load+ind ~4.5 分钟（2018-06-01 起），筹码仅对当日候选计算。

用法：
  python research/gen_529_signal_daily.py                  # 最新可得交易日
  python research/gen_529_signal_daily.py --date 2026-07-31  # 指定日期（校验用）
  python research/gen_529_signal_daily.py --check            # 抽3个历史日做一致性校验
产物：D:/QMT_POOL/529_signal_top16_YYYYMMDD.csv（列 ts_code,atr_pct,rank；ATR 升序前16）
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_12_RPS主升浪/research")

from huangshi_formula_scan import (load_stock_panel, compute_indicators,
                                   pre_signal_529, compute_cost_candidates,
                                   signal_529)

OUT_DIR = "D:/QMT_POOL"
FULL_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16.json"
WARMUP_START = "2018-06-01"   # 与全量表起点一致，保证 MA120/筹码120日窗充足


def atr_pct_series(ind, win=20):
    """20 日 ATR%（真实波幅均值/收盘价），按股票 group 计算（同全量表）。"""
    import pandas as pd
    close = ind["close"]
    high = ind["high"]
    low = ind["low"]
    pc = close.groupby(level="ts_code").shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    atr = tr.groupby(level="ts_code").transform(
        lambda x: x.rolling(win, min_periods=win).mean())
    return atr / close * 100.0


def compute_for_date(target_date, df_full=None, ind_full=None):
    """计算目标日信号 top16。返回 (codes, atr_list) 或 (None, None) 当日无信号。"""
    import pandas as pd
    if df_full is None or ind_full is None:
        df_full = load_stock_panel(WARMUP_START, str(target_date))
        ind_full = compute_indicators(df_full)
    d = pd.Timestamp(target_date)

    pre529 = pre_signal_529(ind_full)
    # 筹码：仅对目标日候选计算（历史候选无需重算，PIT 只用到截至当日）。
    # 注意：pre&chip 对齐会把 sig 索引重排为日期主序，必须按各 Series 自身索引掩码取当日。
    pre_mask = pre529.index.get_level_values("trade_date") == d
    cand_today = pre529[pre_mask]
    cost5, cost95 = compute_cost_candidates(df_full, cand_today)
    sig = signal_529(ind_full, cost5, cost95)
    sig_mask = sig.index.get_level_values("trade_date") == d
    sig_today = sig[sig_mask]
    if int(sig_today.sum()) == 0:
        return None, None

    atr = atr_pct_series(ind_full)
    atr_sig = atr.loc[sig_today[sig_today].index].dropna()
    if len(atr_sig) == 0:
        return None, None
    vals = atr_sig.sort_values()
    codes = vals.index.get_level_values("ts_code").tolist()[:16]
    atr_vals = [float(v) for v in vals.values[:16]]
    return codes, atr_vals


def write_csv(target_date, codes, atr_vals):
    if codes is None:
        fname = "529_signal_top16_%s.csv" % str(target_date).replace("-", "")
        path = os.path.join(OUT_DIR, fname)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts_code", "atr_pct", "rank"])
            w.writerow([])  # 空表头（当日无信号，QMT 侧 fail-open）
        return path, 0
    fname = "529_signal_top16_%s.csv" % str(target_date).replace("-", "")
    path = os.path.join(OUT_DIR, fname)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "atr_pct", "rank"])
        for rank, (c, a) in enumerate(zip(codes, atr_vals), start=1):
            w.writerow([c, "%.4f" % a, rank])
    return path, len(codes)


def consistency_check(dates):
    """抽历史日：增量版 top16 vs 全量表当日切片，前12必须逐只一致（允许尾部并列差异）。"""
    import json
    with open(FULL_TABLE, "r", encoding="utf-8") as f:
        full = json.load(f)
    ok = True
    for d in dates:
        codes, _ = compute_for_date(d)
        full_codes = full.get(d, [])
        if codes is None:
            print("[CHECK] %s 增量=空, 全量=%d只 -> %s" % (d, len(full_codes),
                  "OK" if not full_codes else "FAIL(全量非空)"))
            if full_codes:
                ok = False
            continue
        top12 = codes[:12]
        full12 = full_codes[:12]
        match = top12 == full12
        set_match = set(codes) == set(full_codes)
        print("[CHECK] %s top12匹配=%s 集合匹配=%s 增量top16=%s" % (
            d, match, set_match, codes))
        if top12 != full12:
            print("   增量前12: %s" % top12)
            print("   全量前12: %s" % full12)
            ok = False
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="指定交易日 YYYY-MM-DD（默认=最新可得）")
    ap.add_argument("--check", action="store_true", help="抽3个历史日做一致性校验")
    args = ap.parse_args()

    t0 = time.time()
    if args.check:
        dates = ["2026-07-31", "2025-12-31", "2024-06-28"]
        print("=== 一致性校验 %s ===" % dates)
        ok = consistency_check(dates)
        print("=== 校验结果: %s (%.0fs) ===" % ("PASS" if ok else "FAIL", time.time() - t0))
        sys.exit(0 if ok else 1)

    if args.date:
        target = args.date
    else:
        import pandas as pd
        df_dates = pd.read_parquet(
            "E:/astock/daily/stock_daily.parquet", columns=[]).index.get_level_values("trade_date")
        target = str(sorted(set(df_dates))[-1])[:10]
    print("=== 529 信号日更: 目标日 %s ===" % target)

    df_full = load_stock_panel(WARMUP_START, target)
    ind_full = compute_indicators(df_full)
    codes, atr_vals = compute_for_date(target, df_full, ind_full)
    path, n = write_csv(target, codes, atr_vals)
    print("[%s] 信号 %d 只 -> %s" % (target, n, path))
    if codes:
        print("  top16: %s" % codes)
    print("[total] %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
