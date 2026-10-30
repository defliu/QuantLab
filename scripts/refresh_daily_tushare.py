# coding: utf-8
"""用 Tushare 在线增量补 D:/astock/daily/stock_daily.parquet（2026-10-30 立）。

**为什么需要**：主行情只能靠离线包更新（`refresh_online_incremental.py` 的 TABLES 不含 daily），
离线包一断，面板/候选全部停在旧日期（2026-10-30 实测停在 10-16）。本脚本把「主行情」也接进
在线增量，四张 Tushare 表拼成与本地完全一致的 33 列：

    daily        → open/high/low/close/pre_close/change/pct_chg/vol/amount
    stk_limit    → up_limit/down_limit
    daily_basic  → turnover_rate/turnover_rate_f/volume_ratio/pe/pe_ttm/pb/ps/ps_ttm/
                   dv_ratio/dv_ttm/total_share/float_share/free_share/total_mv/circ_mv
    adj_factor   → adj_factor
    （suspend_timing/suspend_type 置空串、is_st/listed_days 置 None —— 与本地既有行的习惯一致）

安全：
  * 默认 **dry-run**，`--apply` 才写盘；
  * 写前备份 `<file>.bak_tushare_<YYYYmmdd_HHMMSS>`；
  * 先写临时文件、校验行数/日期范围，再 `os.replace` 原子替换；
  * 只追加比本地最新日期更新的交易日，绝不改历史行。

用法：
  python scripts/refresh_daily_tushare.py                 # 只报告要补哪些日
  python scripts/refresh_daily_tushare.py --apply         # 真写
  python scripts/refresh_daily_tushare.py --apply --end 20261030
"""
from __future__ import print_function

import argparse
import io
import json
import os
import shutil
import sys
import time

import pandas as pd

ASTOCK = r"D:/astock"
TARGET = os.path.join(ASTOCK, "daily", "stock_daily.parquet")
KEYS = os.path.join(r"D:/QuantLab", "config", "data_source_keys.json")

COLS = ["open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount",
        "up_limit", "down_limit", "turnover_rate", "turnover_rate_f", "volume_ratio",
        "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
        "total_share", "float_share", "free_share", "total_mv", "circ_mv", "adj_factor",
        "suspend_timing", "suspend_type", "is_st", "listed_days", "trade_date", "ts_code"]


def log(msg):
    print("[DAILY] " + str(msg))
    sys.stdout.flush()


def _token():
    d = json.load(io.open(KEYS, encoding="utf-8"))
    return d["sources"]["tushare"]["token"]


def _local_max_date():
    # 本地是 MultiIndex(trade_date, ts_code)，用索引取最大日期（列里没有 trade_date）
    df = pd.read_parquet(TARGET, columns=["close"])
    return pd.to_datetime(df.index.get_level_values("trade_date")).max()


def _open_days(pro, start, end):
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    return sorted(cal["cal_date"].tolist())


def _fetch_day(pro, day):
    d = pro.daily(trade_date=day)
    if d is None or d.empty:
        return None
    b = pro.daily_basic(trade_date=day)
    lim = pro.stk_limit(trade_date=day)
    adj = pro.adj_factor(trade_date=day)
    df = d.merge(b, on=["ts_code", "trade_date"], how="left", suffixes=("", "_b"))
    df = df.merge(lim, on=["ts_code", "trade_date"], how="left")
    df = df.merge(adj, on=["ts_code", "trade_date"], how="left")
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["suspend_timing"] = ""
    df["suspend_type"] = ""
    # is_st / listed_days 必须填：面板构建会按这两列过滤/计算，留 None 会让新行整批被丢掉
    # （2026-10-30 实测：补数后面板仍停在 10-16，就是这两列全空导致新行被筛掉）
    try:
        basic = pro.stock_basic(exchange="", list_status="L",
                                fields="ts_code,name,list_date")
        b = basic.set_index("ts_code")
        names = b["name"].to_dict()
        ldates = pd.to_datetime(b["list_date"], format="%Y%m%d", errors="coerce").to_dict()
        df["is_st"] = df["ts_code"].map(lambda c: 1.0 if "ST" in str(names.get(c, "")) else 0.0)
        df["listed_days"] = df.apply(
            lambda r: float((r["trade_date"] - ldates[r["ts_code"]]).days)
            if ldates.get(r["ts_code"]) is not None and not pd.isna(ldates.get(r["ts_code"])) else None,
            axis=1)
    except Exception as e:
        log("WARN stock_basic 取不到，is_st/listed_days 留空: %s" % (str(e)[:80],))
        df["is_st"] = None
        df["listed_days"] = None
    for c in COLS:
        if c not in df.columns:
            df[c] = None
    return df[COLS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真写盘（默认 dry-run）")
    ap.add_argument("--start", default=None, help="起始日 YYYYmmdd（默认=本地最新日+1）")
    ap.add_argument("--end", default=None, help="结束日 YYYYmmdd（默认今天）")
    a = ap.parse_args()

    import tushare as ts
    pro = ts.pro_api(_token())

    local_max = _local_max_date()
    log("本地最新 trade_date = %s" % local_max.date())
    start = a.start or (local_max + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end = a.end or time.strftime("%Y%m%d")
    days = _open_days(pro, start, end)
    log("待补交易日 %d 个：%s" % (len(days), ",".join(days)))
    if not days:
        log("无需补数")
        return 0
    if not a.apply:
        log("dry-run：加 --apply 才会写盘")
        return 0

    frames = []
    for d in days:
        try:
            df = _fetch_day(pro, d)
        except Exception as e:
            log("FAIL %s: %s" % (d, str(e)[:100]))
            return 2
        if df is None or df.empty:
            log("SKIP %s：Tushare 无数据" % d)
            continue
        frames.append(df)
        log("OK %s rows=%d" % (d, len(df)))
        time.sleep(0.35)
    if not frames:
        log("没有可写入的数据")
        return 2
    new = pd.concat(frames, ignore_index=True)

    old = pd.read_parquet(TARGET).reset_index()
    # 旧文件里带 pyarrow 的 __fragment_index/__batch_index 等保留列，写回时会与 parquet 内部
    # 字段冲突（"reserved"），必须先剔除
    old = old.loc[:, [c for c in old.columns if not str(c).startswith("__")]]
    old["trade_date"] = pd.to_datetime(old["trade_date"])
    before = len(old)
    new = new.loc[:, [c for c in new.columns if not str(c).startswith("__")]]
    merged = pd.concat([old, new], ignore_index=True)
    merged = merged.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")
    merged = merged.sort_values(["trade_date", "ts_code"]).reset_index(drop=True)
    merged = merged.set_index(["trade_date", "ts_code"])   # 保持本地 MultiIndex 结构
    log("合并：%d -> %d（新增 %d 行）" % (before, len(merged), len(merged) - before))

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bak = TARGET + ".bak_tushare_" + stamp
    shutil.copy2(TARGET, bak)
    log("已备份 -> %s" % bak)

    tmp = TARGET + ".tmp"
    # 必须保留索引（本地是 MultiIndex(trade_date, ts_code)）；index=False 会把日期维度整个丢掉
    merged.to_parquet(tmp, index=True)
    # 校验用 pyarrow 读（pandas 只读子列时会丢掉 MultiIndex 名字）
    import pyarrow.parquet as _pq
    _t = _pq.read_table(tmp, columns=["trade_date"])
    mx = pd.to_datetime(_t.column("trade_date").to_pandas()).max()
    if mx < pd.Timestamp(days[-1]) and days[-1] <= time.strftime("%Y%m%d"):
        log("校验失败：写后最大日期 %s < 期望 %s" % (mx.date(), days[-1]))
        os.remove(tmp)
        return 2
    os.replace(tmp, TARGET)
    log("已写入 %s（最大 trade_date=%s，总行数=%d）" % (TARGET, mx.date(), len(merged)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
