# -*- coding: utf-8 -*-
"""数据质量快速巡检（快速版）：日线全量 + 财务PIT/重复/缺失 + 分钟只扫覆盖"""
import os
import sys
import time
import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np
import pyarrow.parquet as pq

ASTOCK = "E:/astock"
REPORT = []

def log(msg):
    print(msg)
    REPORT.append(msg)

def ts(s):
    return time.strftime("%H:%M:%S") + " " + s

# ── 交易日历（简易版，从日线提取） ──
def trading_calendar_from_daily(df):
    dates = df["trade_date"].dt.date.unique()
    return sorted(dates)

# ── 日线检查 ──
def check_daily():
    log(ts("=== 日线检查 ==="))
    t0 = time.time()
    path = os.path.join(ASTOCK, "daily", "stock_daily.parquet")
    df = pd.read_parquet(path)
    # ts_code/trade_date 可能在 index 或 columns，统一拉到 columns
    for col in ["ts_code", "trade_date"]:
        if col in df.index.names:
            df = df.reset_index(col)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    log(ts("读取完成 %.1fs, %d行 %d列") % (time.time()-t0, len(df), len(df.columns)))
    
    # 1. 主键重复
    dup = df.duplicated(subset=["ts_code", "trade_date"]).sum()
    log("[日线-重复] (ts_code, trade_date) 重复行: %d" % dup)
    
    # 2. OHLC约束
    ohlc_violate = ((df["low"] > df["open"]) | (df["low"] > df["close"]) | 
                     (df["high"] < df["open"]) | (df["high"] < df["close"])).sum()
    log("[日线-OHLC] 违反 low<=O,C<=high: %d行" % ohlc_violate)
    
    # 3. 涨跌幅一致性
    valid_pre = df["pre_close"] > 0
    calc_pct = (df.loc[valid_pre, "close"] / df.loc[valid_pre, "pre_close"] - 1) * 100
    diff_pct = (calc_pct - df.loc[valid_pre, "pct_chg"]).abs()
    pct_violate = (diff_pct > 0.1).sum()
    log("[日线-涨跌幅] pct_chg vs (close/pre_close-1) 偏差>0.1%%: %d行 (pre_close>0中)" % pct_violate)
    
    # 4. 零/负值
    zero_close = (df["close"] <= 0).sum()
    neg_vol = (df["vol"] < 0).sum()
    log("[日线-零负值] close<=0: %d, vol<0: %d" % (zero_close, neg_vol))
    
    # 5. 涨跌停突破（非ST/非新股）
    has_st = "is_st" in df.columns
    has_listed = "listed_days" in df.columns
    st_mask = df["is_st"] == 1 if has_st else pd.Series(False, index=df.index)
    listed_mask = df["listed_days"] <= 5 if has_listed else pd.Series(False, index=df.index)
    normal = ~st_mask & ~listed_mask & (df["pct_chg"].notna()) & (df["pct_chg"].abs() < 1000)
    over_limit = (df.loc[normal, "pct_chg"].abs() > 11).sum()
    log("[日线-涨跌停突破] 非ST非新股|pct_chg|>11%%: %d行" % over_limit)
    
    # 6. 成交量突变（vol > 50x 20日均值）
    df_sorted = df.sort_values(["ts_code", "trade_date"])
    vol_ma20 = df_sorted.groupby("ts_code")["vol"].transform(
        lambda x: x.rolling(20, min_periods=10).mean())
    spike = (df_sorted["vol"] > vol_ma20 * 50) & (vol_ma20 > 0)
    log("[日线-成交量突变] vol>50x MA20: %d行" % spike.sum())
    
    # 7. 缺交易日（按股票样本检查）
    cal = trading_calendar_from_daily(df)
    all_dates = set(cal)
    sample_codes = df["ts_code"].unique()[:50]
    missing_stats = []
    for code in sample_codes:
        code_df = df[df["ts_code"] == code]
        d = set(code_df["trade_date"].dt.date)
        first_date = min(d)
        last_date = max(d)
        expected = {dd for dd in all_dates if first_date <= dd <= last_date}
        missing = expected - d
        if missing:
            missing_stats.append(len(missing))
    if missing_stats:
        log("[日线-缺交易日] 50只样本: %d只有缺失, 缺失天数分布: median=%d max=%d" % 
            (len(missing_stats), np.median(missing_stats), max(missing_stats)))
    else:
        log("[日线-缺交易日] 50只样本: 无缺失")
    
    # 8. 复权因子单调性（应只增不减）
    adj_issues = 0
    adj_total = 0
    for code in sample_codes:
        code_df = df[df["ts_code"] == code].sort_values("trade_date")
        if "adj_factor" not in code_df.columns:
            continue
        adj_total += 1
        adj = code_df["adj_factor"].values.astype(float)
        decreases = np.sum(np.diff(adj) < -1e-8)
        if decreases > 0:
            adj_issues += 1
    log("[日线-复权因子] %d只样本: %d只存在adj_factor非单调递增" % (adj_total, adj_issues))
    
    # 9. 日期范围
    log("[日线-日期范围] %s ~ %s" % (df["trade_date"].min(), df["trade_date"].max()))
    log("[日线-股票数] %d" % df["ts_code"].nunique())
    
    log(ts("日线检查完成 %.1fs") % (time.time()-t0))

# ── 财务检查 ──
def check_finance():
    log(ts("\n=== 财务检查 ==="))
    t0 = time.time()
    finance_dir = os.path.join(ASTOCK, "finance")
    
    for fname in sorted(os.listdir(finance_dir)):
        if not fname.endswith(".parquet"):
            continue
        path = os.path.join(finance_dir, fname)
        t1 = time.time()
        df = pd.read_parquet(path)
        log("\n--- %s (%d行) ---" % (fname, len(df)))
        
        # 统一 index→columns
        for col in ["ts_code", "end_date", "ann_date", "f_ann_date"]:
            if col in df.index.names:
                df = df.reset_index(col)
        
        # 日期列转 datetime
        for col in ["end_date", "ann_date", "f_ann_date"]:
            if col in df.columns and df[col].dtype == object:
                try:
                    df[col] = pd.to_datetime(df[col])
                except Exception:
                    pass
        
        # 主键检测
        if "ts_code" in df.columns and "end_date" in df.columns:
            dup = df.duplicated(subset=["ts_code", "end_date"]).sum()
            log("  (ts_code, end_date) 重复: %d行" % dup)
        
        # PIT: ann_date 缺失
        if "ann_date" in df.columns:
            null_ann = df["ann_date"].isna().sum()
            log("  ann_date 缺失: %d行 (%.2f%%)" % (null_ann, 100*null_ann/len(df)))
            
            # ann_date < end_date（披露日早于报告期）
            if "end_date" in df.columns:
                both_valid = df["ann_date"].notna() & df["end_date"].notna()
                if both_valid.sum() > 0:
                    early_ann = (df.loc[both_valid, "ann_date"] < df.loc[both_valid, "end_date"]).sum()
                    log("  ann_date < end_date: %d行" % early_ann)
        
        # update_flag: 同一报告期多次披露
        if "update_flag" in df.columns and "ts_code" in df.columns and "end_date" in df.columns:
            updated = df[df["update_flag"] != 0]
            log("  update_flag!=0 (修正/重述): %d行" % len(updated))
        
        # 缺季检查（fina_indicator为主）
        if fname == "fina_indicator.parquet":
            if "ts_code" in df.columns and "end_date" in df.columns:
                df["year"] = df["end_date"].dt.year
                df["quarter"] = df["end_date"].dt.quarter
                code_year_q = df.groupby("ts_code").apply(
                    lambda x: set(zip(x["year"], x["quarter"])))
                expected_q = {(y, q) for y in range(2019, 2026) for q in range(1, 5)}
                missing_q_counts = []
                for code, yq_set in code_year_q.items():
                    if yq_set:
                        first_year = min(y for y, q in yq_set)
                        if first_year <= 2019:
                            missing = expected_q - yq_set
                            if missing:
                                missing_q_counts.append(len(missing))
                if missing_q_counts:
                    log("  季报缺失(2019-2025): %d只股票有缺失, 缺失季数 median=%d max=%d" %
                        (len(missing_q_counts), np.median(missing_q_counts), max(missing_q_counts)))
        
        log("  读取+检查 %.1fs" % (time.time()-t1))
    
    log(ts("财务检查完成 %.1fs") % (time.time()-t0))

# ── 分钟线覆盖检查 ──
def check_minute_coverage():
    log(ts("\n=== 分钟线覆盖检查 ==="))
    t0 = time.time()
    minute_dir = os.path.join(ASTOCK, "minute")
    
    for freq in sorted(os.listdir(minute_dir)):
        d = os.path.join(minute_dir, freq)
        if not os.path.isdir(d):
            continue
        files = [f for f in os.listdir(d) if f.endswith(".parquet")]
        if not files:
            log("%s/: 空目录" % freq)
            continue
        
        # 采样：首5 + 末1
        sample_files = sorted(files)[:5] + [sorted(files)[-1]]
        total_rows = 0
        min_dt = None
        max_dt = None
        empty_files = 0
        for f in sample_files:
            path = os.path.join(d, f)
            pf = pq.ParquetFile(path)
            nrows = pf.metadata.num_rows
            total_rows += nrows
            if nrows == 0:
                empty_files += 1
            schema = pf.schema
            dt_cols = [n for n in schema.names if "time" in n.lower() or "date" in n.lower()]
            if dt_cols:
                col = pf.read(columns=[dt_cols[0]]).column(dt_cols[0])
                import pyarrow.compute as pc
                mx = pc.max(col).as_py()
                mn = pc.min(col).as_py()
                if max_dt is None or (mx and mx > max_dt):
                    max_dt = mx
                if min_dt is None or (mn and mn < min_dt):
                    min_dt = mn
        
        log("%s/: %d文件, 范围 %s ~ %s, 空文件: %d" % 
            (freq, len(files), min_dt, max_dt, empty_files))
    
    log(ts("分钟线检查完成 %.1fs") % (time.time()-t0))

# ── basic检查 ──
def check_basic():
    log(ts("\n=== 基础信息检查 ==="))
    path = os.path.join(ASTOCK, "basic", "stock_basic.parquet")
    df = pd.read_parquet(path)
    log("股票数: %d" % len(df))
    if "list_date" in df.columns:
        log("上市日期范围: %s ~ %s" % (df["list_date"].min(), df["list_date"].max()))
    if "delist_date" in df.columns:
        delisted = df["delist_date"].notna().sum()
        log("已退市: %d" % delisted)
    log("列: %s" % list(df.columns))

# ── main ──
if __name__ == "__main__":
    t_start = time.time()
    log("=" * 60)
    log("数据质量快速巡检 - %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("数据源: %s" % ASTOCK)
    log("=" * 60)
    
    try:
        check_daily()
    except Exception as e:
        log(ts("日线检查异常: %s") % e)
    
    try:
        check_finance()
    except Exception as e:
        log(ts("财务检查异常: %s") % e)
    
    try:
        check_minute_coverage()
    except Exception as e:
        log(ts("分钟线检查异常: %s") % e)
    
    try:
        check_basic()
    except Exception as e:
        log(ts("基础信息检查异常: %s") % e)
    
    log(ts("\n=== 全部完成 %.1fs ===") % (time.time()-t_start))
    
    # 写报告
    report_path = os.path.join(ASTOCK, "data_quality_report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(REPORT))
    log("报告已写入: %s" % report_path)
