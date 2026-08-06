# -*- coding: utf-8 -*-
"""生成 V2 策略 QMT 运行所需的预生成 CSV (D:/QMT_POOL)

数据源: 更新后的本地数据
  - daily/stock_daily.parquet  (已更新到 2026-07-31)
  - basic/stock_basic.parquet  (已更新)
  - finance/fina_indicator.parquet (已更新到 2026 半年报)

产物 (与 strategy_v2.py 的 _load_* 消费格式严格一致):
  - financial_pb.csv        ts_code,value    最新交易日 PB
  - financial_pe_ttm.csv    ts_code,value    最新交易日 PE_TTM
  - financial_circ_mv.csv   ts_code,value    最新交易日流通市值(万元)
  - industry_map.csv        ts_code,industry 申万行业 (来自 stock_basic)
  - bp_hist_pct.csv         ts_code,date,hp_pct  月度 BP 值(1/PB, 月末)
       注意: hp_pct 存的是【月度 BP 值】而非分位!
       strategy_v2._score_stocks 把该值当 BP 值, 数过去36月内 h<=latest 的比例
       得历史分位, 与回测 scoring.py 口径一致. 若存分位则算出"分位的分位", 因子失效.
  - selected.txt            股票池(全部A股代码, QMT实时接口的fallback)

用法: python scripts/gen_qmt_csv.py
"""
import os
import sys
import csv
import time

import pandas as pd
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

DAILY = r"E:\astock\daily\stock_daily.parquet"
BASIC = r"E:\astock\basic\stock_basic.parquet"
OUT_DIR = r"D:\QMT_POOL"

N_MONTHS = 36   # 历史分位窗口 (策略 HP_WINDOW)
DATE_0 = "2010-01-31"  # 起点: 保证回看窗口足够 (daily 2009 起)


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def gen_valuation():
    """financial_pb / financial_pe_ttm / financial_circ_mv: 最新交易日快照"""
    log("读取 daily 最新交易日...")
    t0 = time.time()
    df = pd.read_parquet(DAILY)
    latest = df.index.get_level_values("trade_date").max()
    last = df.xs(latest, level="trade_date")
    log("  最新交易日: %s, %d 只 (%ds)" % (latest.date(), len(last), time.time() - t0))

    specs = [
        ("financial_pb.csv", "pb", "PB"),
        ("financial_pe_ttm.csv", "pe_ttm", "PE_TTM"),
        ("financial_circ_mv.csv", "circ_mv", "流通市值(万元)"),
        ("financial_total_mv.csv", "total_mv", "总市值(万元)"),
    ]
    for fname, col, label in specs:
        if col not in last.columns:
            log("  跳过 %s: daily 无 %s 列" % (fname, col))
            continue
        sub = last[[col]].dropna()
        sub = sub[sub[col] > 0]
        path = os.path.join(OUT_DIR, fname)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["ts_code", "value"])
            for code, v in sub[col].items():
                w.writerow([code, "%.4f" % v])
        log("  [OK] %s: %d 只 (%s)" % (fname, len(sub), label))


def gen_industry():
    """industry_map.csv: 来自 stock_basic.industry"""
    t0 = time.time()
    b = pd.read_parquet(BASIC)
    if "industry" not in b.columns:
        log("  [SKIP] stock_basic 无 industry 列")
        return
    sub = b[["ts_code", "industry"]].dropna(subset=["ts_code"])
    sub = sub[sub["industry"].notna() & (sub["industry"].astype(str) != "")]
    path = os.path.join(OUT_DIR, "industry_map.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "industry"])
        for code, ind in sub[["ts_code", "industry"]].itertuples(index=False):
            w.writerow([code, ind])
    log("  [OK] industry_map.csv: %d 只 (%ds)" % (len(sub), time.time() - t0))


def gen_bp_hist():
    """bp_hist_pct.csv: 月度 BP 值(1/PB, 月末). 注意不是分位!"""
    t0 = time.time()
    log("读取 daily pb 全历史...")
    df = pd.read_parquet(DAILY, columns=["pb"])
    df = df.reset_index()
    df["date"] = pd.to_datetime(df["trade_date"])
    log("  全历史 %d 行 (%ds)" % (len(df), time.time() - t0))

    rows = []
    bp_cache = {}
    # 逐 code 处理 (内存友好)
    n_codes = 0
    for code, g in df.groupby("ts_code"):
        n_codes += 1
        pb = g.set_index("date")["pb"].replace(0, np.nan).dropna()
        pb.index = pd.to_datetime(pb.index)
        pb = pb[~pb.index.duplicated(keep="last")].sort_index()
        if len(pb) == 0:
            continue
        bp_m = (1.0 / pb).resample("ME").last().dropna()
        bp_m = bp_m[bp_m.index >= pd.Timestamp(DATE_0)]
        if len(bp_m) == 0:
            continue
        for dt, bp in bp_m.items():
            rows.append((code, dt.strftime("%Y-%m-%d"), "%.4f" % bp))
        if n_codes % 500 == 0:
            log("  %d/%d codes (%ds)" % (n_codes, df["ts_code"].nunique(), time.time() - t0))
    log("  计算完成: %d 行 (%ds)" % (len(rows), time.time() - t0))

    path = os.path.join(OUT_DIR, "bp_hist_pct.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "date", "hp_pct"])
        w.writerows(rows)
    log("  [OK] bp_hist_pct.csv: %d 行" % len(rows))


def gen_selected():
    """selected.txt: 全部A股代码 (QMT实时接口 fallback)"""
    t0 = time.time()
    b = pd.read_parquet(BASIC)
    codes = sorted(b["ts_code"].tolist())
    path = os.path.join(OUT_DIR, "selected.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(codes) + "\n")
    log("  [OK] selected.txt: %d 只 (%ds)" % (len(codes), time.time() - t0))


def gen_delist_info():
    """delist_info.csv: ts_code,list_status,delist_date (退市排雷用)
    v2.3 讨论室组件A: 纯BP会把濒临退市深度折价股排到前列(长生退等BP第1),
    QMT 端需 delist_date 做"退市临近"剔除。list_status: L上市/D退市/P暂停。"""
    t0 = time.time()
    b = pd.read_parquet(BASIC)
    need = ["ts_code", "list_status", "delist_date"]
    missing = [c for c in need if c not in b.columns]
    if missing:
        log("  [SKIP] delist_info: stock_basic 缺列 %s" % missing)
        return
    sub = b[need].copy()
    # delist_date 规范化为 YYYY-MM-DD 字符串 (空则留空)
    def _fmt(v):
        if v is None:
            return ""
        s = str(v).strip()
        if s in ("", "nan", "None", "NaT"):
            return ""
        s = s.replace("-", "").split(" ")[0][:8]
        if len(s) == 8 and s.isdigit():
            return "%s-%s-%s" % (s[0:4], s[4:6], s[6:8])
        return ""
    sub["delist_date"] = sub["delist_date"].map(_fmt)
    sub["list_status"] = sub["list_status"].fillna("").astype(str)
    path = os.path.join(OUT_DIR, "delist_info.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_code", "list_status", "delist_date"])
        for code, st, dd in sub[["ts_code", "list_status", "delist_date"]].itertuples(index=False):
            w.writerow([code, st, dd])
    n_d = int((sub["delist_date"] != "").sum())
    log("  [OK] delist_info.csv: %d 只 (含退市日 %d) (%ds)" % (len(sub), n_d, time.time() - t0))


def main():
    log("=== 生成 V2 策略 QMT CSV ===")
    gen_valuation()
    gen_industry()
    gen_bp_hist()
    gen_selected()
    gen_delist_info()
    log("=== 完成 ===")


if __name__ == "__main__":
    main()
