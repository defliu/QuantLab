# coding: utf-8
"""增量行情拉取：miniQMT xtdata → 临时增量库（不碰主数据）。

设计原则：
  - 主数据 E:/astock（周更权威快照）只读，绝不修改
  - 每日临时拉取的行情单独存入 data_live/incremental_daily.parquet
  - 可重复拉取（重建临时库），可丢弃，不污染主库

用法：
  python xtdata_update.py --start 2026-08-15 --end 2026-08-20    # 全市场增量
  python xtdata_update.py --start 2026-08-15 --limit 100         # 调试(前100只)
  python xtdata_update.py                                        # 自动: 主库最后日+1 到 今天
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import data_config as DC
import qmt_config as C

sys.path.append(C.XTPACK)

HERE = DC.PROJECT_DIR
LIVE_DIR = DC.LIVE_DIR
MAIN_DAILY = DC.MAIN_DAILY
OUT_INCR = os.path.join(LIVE_DIR, "incremental_daily.parquet")
OUT_META = os.path.join(LIVE_DIR, "update_meta.json")

FIELDS_KEEP = ["open", "high", "low", "close", "volume", "amount", "preClose", "suspendFlag"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None, help="增量起始日期(YYYY-MM-DD)，默认主库最后交易日+1")
    ap.add_argument("--end", default=None, help="增量结束日期，默认今天")
    ap.add_argument("--limit", type=int, default=0, help="仅前N只股票(调试)")
    args = ap.parse_args()
    os.makedirs(LIVE_DIR, exist_ok=True)

    from xtquant import xtdata

    # 主库最后日期
    main_df = pd.read_parquet(MAIN_DAILY, columns=[])
    last_date = main_df.index.get_level_values("trade_date").max()
    start = args.start or (last_date + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end = args.end or time.strftime("%Y%m%d")
    start_s = start.replace("-", "")
    end_s = end.replace("-", "")
    print(f"[1/4] 主库最后交易日: {last_date.date()} | 增量区间: {start_s} ~ {end_s}")

    # 股票列表（主库全市场代码，保证与主库口径一致）
    codes = list(dict.fromkeys(main_df.index.get_level_values("ts_code").astype(str).tolist()))
    if args.limit:
        codes = codes[: args.limit]
    print(f"[2/4] 股票数: {len(codes)}，先 download_history_data 下载区间 ...")

    # 关键：xtdata 本地缓存可能落后，必须先按区间下载，再 get_market_data_ex 才能读到
    n_dl = 0
    for i, code in enumerate(codes):
        try:
            xtdata.download_history_data(code, period="1d", start_time=start_s, end_time=end_s)
            n_dl += 1
            if i % 500 == 0:
                print(f"    已下载 {i}/{len(codes)} ...")
        except Exception:
            pass
    print(f"    下载完成 {n_dl}/{len(codes)}")

    print("[3/4] xtdata 读取增量日线 ...")
    data = xtdata.get_market_data_ex(
        [], codes, period="1d",
        start_time=start_s, end_time=end_s,
        dividend_type="none",
    )
    frames = []
    for code, df in data.items():
        if df is None or len(df) == 0:
            continue
        # df: index=日期, columns=字段
        sub = df.copy()
        sub["ts_code"] = code
        sub = sub.reset_index().rename(columns={"index": "trade_date"})
        # 只保留需要的字段（xtdata 有的）
        keep = ["trade_date", "ts_code"] + [c for c in FIELDS_KEEP if c in sub.columns]
        frames.append(sub[keep])
    if not frames:
        print("    !! 无增量数据")
        return
    incr = pd.concat(frames, ignore_index=True)
    # xtdata 返回的日期索引是 YYYYMMDD 整数，需显式转 datetime
    incr["trade_date"] = pd.to_datetime(incr["trade_date"].astype(str), format="%Y%m%d", errors="coerce")
    incr = incr.dropna(subset=["trade_date"])
    for c in incr.columns:
        if c not in ("trade_date", "ts_code"):
            incr[c] = pd.to_numeric(incr[c], errors="coerce")
    print(f"    增量行数: {len(incr):,} | 日期: {incr['trade_date'].min().date()} ~ {incr['trade_date'].max().date()} | 股票: {incr['ts_code'].nunique()}")

    print("[4/4] 写入临时增量库 ...")
    incr.to_parquet(OUT_INCR, index=False)
    meta = {
        "last_main_date": str(last_date.date()),
        "incremental_range": [start_s, end_s],
        "source": "miniQMT xtdata",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_rows": len(incr),
        "n_stocks": int(incr["ts_code"].nunique()),
        "fields": [c for c in FIELDS_KEEP if c in incr.columns],
    }
    with open(OUT_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("    临时库:", OUT_INCR)
    print("    元信息:", OUT_META)
    print("    主数据 E:/astock 未做任何修改 ✅")


if __name__ == "__main__":
    main()
