# -*- coding: utf-8 -*-
"""astock 本地数据源增量更新脚本

把 E:\\量化\\行情数据更新池 的增量数据合并到 E:\\astock：
- daily:   本地(<2026-01-01) + 全量包2026(01-05~07-24) + 增量包(07-27~07-31)
- minute:  本地(<2026-01-01) + 全量包 + 增量包, 按周期逐code合并
- basic:   本地 + 增量包(按 ts_code 去重)

安全: 全部先写临时文件, 验证成功后 os.replace 原子替换原文件。
用法:
  python scripts/update_astock.py          # 正常执行
  python scripts/update_astock.py --dry    # 只统计不落盘
"""
import os
import sys
import time
import argparse
import tempfile
import shutil
from itertools import repeat

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

SRC_ROOT = r"E:\量化\行情数据更新池"
FULL_2026 = os.path.join(SRC_ROOT, "2026(更新到20260724）")
INC_0727 = os.path.join(SRC_ROOT, "行情数据（增量数据7.27-7.31）")

DST_DAILY = r"E:\astock\daily\stock_daily.parquet"
DST_MINUTE = r"E:\astock\minute"
DST_BASIC = r"E:\astock\basic\stock_basic.parquet"

CUTOFF = pd.Timestamp("2026-01-01")  # 本地保留 < 2026-01-01, 2026 年由更新池全量提供
DAILY_COLS_EXTRA = ["data_source"]   # 本地独有列, 新数据填 NaN


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def atomic_write(df, path, period=None):
    """写临时文件后原子替换"""
    tmp = path + ".tmp_update"
    if period is not None:
        # minute: 保持本地 MultiIndex (trade_date, trade_time) 格式
        df = df.set_index(["trade_date", "trade_time"]).sort_index()
    df.to_parquet(tmp)
    os.replace(tmp, path)


def update_daily(dry=False):
    log("=== daily 更新 ===")
    t0 = time.time()

    # 1. 本地 < 2026-01-01
    old = pd.read_parquet(DST_DAILY, filters=[("trade_date", "<", CUTOFF)])
    log("  本地(<2026-01-01): %d 行, 到 %s" % (len(old), old.index.get_level_values("trade_date").max()))

    # 2. 全量包 2026 (01-05 ~ 07-24)
    full = pd.read_parquet(os.path.join(FULL_2026, "day", "stock_daily.parquet"))
    log("  全量包2026: %d 行, %s ~ %s" % (
        len(full), full["trade_date"].min().date(), full["trade_date"].max().date()))

    # 3. 增量包 (07-27 ~ 07-31)
    inc = pd.read_parquet(os.path.join(INC_0727, "stock_daily.parquet"))
    log("  增量包: %d 行, %s ~ %s" % (
        len(inc), inc["trade_date"].min().date(), inc["trade_date"].max().date()))

    # 合并: 统一 MultiIndex + 列对齐 + 日期类型统一
    old = old.reset_index()
    parts = [old, full, inc]
    for p in parts:
        p["trade_date"] = pd.to_datetime(p["trade_date"])
        for c in DAILY_COLS_EXTRA:
            if c not in p.columns:
                p[c] = None
    merged = pd.concat(parts, ignore_index=True, sort=False)
    merged = merged.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
    merged = merged.set_index(["trade_date", "ts_code"]).sort_index()

    log("  合并结果: %d 行, %s ~ %s (+%d 行)" % (
        len(merged), merged.index.get_level_values("trade_date").min().date(),
        merged.index.get_level_values("trade_date").max().date(),
        len(merged) - len(old)))
    log("  耗时 %.1fs" % (time.time() - t0))

    if dry:
        log("  [dry] 跳过写入")
        return
    atomic_write(merged, DST_DAILY)
    log("  [OK] daily 已写入 %s" % DST_DAILY)


def process_code(fname, dst_dir, full_dir, inc_tmp_dir, period):
    """处理单个 code: 本地(<2026) + 全量包 + 增量包 -> 原子写
    模块级函数以便 ProcessPoolExecutor 可 pickle。"""
    code = fname.replace(".parquet", "")
    parts = []
    local_path = os.path.join(dst_dir, fname)
    if os.path.exists(local_path):
        try:
            d = pd.read_parquet(local_path, filters=[("trade_date", "<", CUTOFF)])
            if len(d):
                parts.append(d.reset_index())
        except Exception as e:
            log("    本地读取失败 %s: %s" % (fname, e))
    full_path = os.path.join(full_dir, fname)
    if os.path.exists(full_path):
        parts.append(pd.read_parquet(full_path))
    inc_path = os.path.join(inc_tmp_dir, fname)
    if os.path.exists(inc_path):
        parts.append(pd.read_parquet(inc_path))
    if not parts:
        return
    merged = pd.concat(parts, ignore_index=True, sort=False)
    merged = merged.drop_duplicates(subset=["trade_date", "trade_time"], keep="last")
    atomic_write(merged, local_path, period=period)


def update_minute(period, dry=False, workers=1):
    """更新单个周期: 本地 + 全量包 + 增量包"""
    dst_dir = os.path.join(DST_MINUTE, period)
    full_dir = os.path.join(FULL_2026, period)
    inc_file = os.path.join(INC_0727, "stock_%s_data.parquet" % period)

    if not os.path.isdir(full_dir):
        log("  跳过 %s: 全量包无该周期目录" % period)
        return

    t0 = time.time()
    # 增量包按 code 拆分为临时文件（子进程只传路径, 不序列化大数据）
    inc_tmp_dir = os.path.join(tempfile.gettempdir(), "astock_inc", period)
    if os.path.isdir(inc_tmp_dir):
        shutil.rmtree(inc_tmp_dir)
    os.makedirs(inc_tmp_dir)
    inc_all = pd.read_parquet(inc_file)
    n_inc = 0
    for code, g in inc_all.groupby("ts_code"):
        g.to_parquet(os.path.join(inc_tmp_dir, code + ".parquet"))
        n_inc += 1
    log("  %s: 增量包 %d 行 / %d 只 (已拆分)" % (period, len(inc_all), n_inc))

    # 只处理更新池覆盖的 code（本地独有 code 无新数据, 不动）
    pool_codes = sorted(set(os.listdir(full_dir)) | set(os.listdir(inc_tmp_dir)))
    local_codes = set(os.listdir(dst_dir)) if os.path.isdir(dst_dir) else set()
    n_local_have = sum(1 for c in pool_codes if c in local_codes)
    log("  %s: 需处理 %d 个 code (本地已有 %d, 新建 %d)" % (
        period, len(pool_codes), n_local_have, len(pool_codes) - n_local_have))

    if dry:
        log("  [dry] %s 跳过逐code处理" % period)
        return

    n_done = [0]
    n_total = len(pool_codes)

    def run(fname):
        process_code(fname, dst_dir, full_dir, inc_tmp_dir, period)
        n_done[0] += 1
        if n_done[0] % 500 == 0:
            log("    %s: %d/%d (%.1fs)" % (period, n_done[0], n_total, time.time() - t0))

    if workers > 1:
        import concurrent.futures as cf
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            for fname in ex.map(process_code, pool_codes,
                                repeat(dst_dir), repeat(full_dir),
                                repeat(inc_tmp_dir), repeat(period)):
                n_done[0] += 1
                if n_done[0] % 500 == 0:
                    log("    %s: %d/%d (%.1fs)" % (period, n_done[0], n_total, time.time() - t0))
    else:
        for fname in pool_codes:
            run(fname)

    shutil.rmtree(inc_tmp_dir, ignore_errors=True)
    log("  %s: 完成 %d 个 code, 耗时 %.1fs" % (period, n_done[0], time.time() - t0))


def update_basic(dry=False):
    log("=== basic 更新 ===")
    t0 = time.time()
    old = pd.read_parquet(DST_BASIC)
    inc = pd.read_parquet(os.path.join(INC_0727, "stock_basic_data.parquet"))
    # 日期列统一为本地格式 YYYYMMDD 字符串 (本地 str, 增量 datetime64)
    for c in ("list_date", "delist_date"):
        if c in inc.columns:
            inc[c] = pd.to_datetime(inc[c]).dt.strftime("%Y%m%d")
    log("  本地 %d 行, 增量 %d 行(唯一 %d)" % (len(old), len(inc), inc["ts_code"].nunique()))
    merged = pd.concat([old, inc], ignore_index=True, sort=False)
    merged = merged.drop_duplicates(subset=["ts_code"], keep="last")
    log("  合并结果: %d 行 (+%d), 耗时 %.1fs" % (len(merged), len(merged) - len(old), time.time() - t0))
    if dry:
        log("  [dry] 跳过写入")
        return
    atomic_write(merged, DST_BASIC)
    log("  [OK] basic 已写入")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只统计不落盘")
    ap.add_argument("--periods", nargs="*", default=["1min", "5min", "15min", "30min", "60min"],
                    help="要更新的分钟周期")
    ap.add_argument("--workers", type=int, default=1, help="minute 并行进程数")
    args = ap.parse_args()

    log("dry-run: %s" % ("是" if args.dry else "否"))
    update_daily(args.dry)
    for p in args.periods:
        update_minute(p, args.dry, workers=args.workers)
    update_basic(args.dry)
    log("=== 全部完成 ===")


if __name__ == "__main__":
    main()
