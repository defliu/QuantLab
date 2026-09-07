# coding: utf-8
"""Tushare moneyflow 每日增量刷新 -> D:/astock/moneyflow/moneyflow.parquet（P16 F2 主数据源）。

对齐规则（关键）：
  - Tushare moneyflow 原生 amount 字段单位即「万元」（实测核对：600519.SH 2026-09-02 RAW buy_lg_amount=81094.1、net_mf_amount=-1879.33，均为万元），
    本仓库 parquet 同样以「万元」存储（与 g2 训练口径一致），故 amount 列直接映射、无需换算。
  - 字段名与 Tushare 原生完全一致（buy_lg_amount / buy_elg_amount / net_mf_amount 等），直接映射。
  - index 固定为 ['ts_code', 'trade_date'] 多级索引（与现有 parquet 一致）。

增量策略：
  - 读现有 parquet 最新 trade_date，从次日刷到 T-1（freshness 目标 T+1）。
  - 按 trade_date 拉全市场（分页 limit=5000），合并去重（keep=last），备份旧档后原子写回。

依赖：tushare/pandas/pyarrow（缺失自动 pip 安装到当前 python，T-20260903-003）。
用法：python refresh_tushare_moneyflow.py [--days N] [--dry-run]
"""
import argparse
import json
import os
import sys
import shutil
import subprocess
import importlib

try:
    import pandas as pd
except ImportError:  # pandas 缺失自动安装到当前 python（T-20260903-003）
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pandas"], check=True)
    import pandas as pd

KEY_FILE = "D:/QuantLab/config/data_source_keys.json"
PARQUET = "D:/astock/moneyflow/moneyflow.parquet"
AMOUNT_COLS = ["buy_sm_amount", "sell_sm_amount", "buy_md_amount", "sell_md_amount",
               "buy_lg_amount", "sell_lg_amount", "buy_elg_amount", "sell_elg_amount",
               "net_mf_amount"]
IDX = ["ts_code", "trade_date"]


def ensure_deps():
    """tushare/pyarrow 缺失则自动 pip 安装到当前 python（pandas 已在顶部守卫导入；T-20260903-003）。"""
    for pkg in ("tushare", "pyarrow"):
        try:
            importlib.import_module(pkg)
        except ImportError:
            print("  缺少 %s，自动 pip 安装..." % pkg)
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", pkg], check=True)
            importlib.import_module(pkg)
    import tushare
    return tushare


def load_token():
    with open(KEY_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    s = cfg.get("sources", {}).get("tushare", {})
    tk = s.get("token")
    if not tk or tk == "待填":
        raise SystemExit("tushare.token 未配置：请在 config/data_source_keys.json 的 sources.tushare.token 填入后重试")
    return tk


def fetch_day(pro, date_str, limit=5000):
    frames = []
    offset = 0
    while True:
        df = pro.moneyflow(trade_date=date_str, limit=limit, offset=offset)
        if df is None or df.empty:
            break
        frames.append(df)
        if len(df) < limit:
            break
        offset += limit
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def trade_cal(pro, start, end):
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end,
                            is_open="1", fields="cal_date")
        return cal["cal_date"].tolist()
    except Exception:
        # fallback：工作日近似交易日（非交易日 Tushare 返空，自动跳过）
        bds = pd.bdate_range(start, end).strftime("%Y%m%d").tolist()
        return bds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=15, help="回溯补刷最近 N 个交易日（默认 15）")
    ap.add_argument("--dry-run", action="store_true", help="只打印待刷日期，不写盘")
    args = ap.parse_args()

    ts = ensure_deps()
    token = load_token()
    pro = ts.pro_api(token)

    if os.path.exists(PARQUET):
        exist = pd.read_parquet(PARQUET, columns=["net_mf_amount"])
        latest = pd.Timestamp(exist.index.get_level_values("trade_date").max())
    else:
        latest = pd.Timestamp("2007-01-01")
    start = (latest + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end = (pd.Timestamp.now().normalize() - pd.Timedelta(days=1)).strftime("%Y%m%d")
    if start > end:
        print("已是最新（latest=%s），无需刷新" % latest.date())
        return
    dates = trade_cal(pro, start, end)
    dates = dates[-args.days:]
    if not dates:
        print("区间内无交易日，无需刷新")
        return
    print("待刷 %d 个交易日：%s ... %s" % (len(dates), dates[0], dates[-1]))
    if args.dry_run:
        return

    new_frames = []
    for d in dates:
        df = fetch_day(pro, d)
        if df.empty:
            print("  %s 空，跳过" % d)
            continue
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        # 单位已是万元（Tushare moneyflow 原生 amount=万元，与 parquet/训练口径一致），无需换算
        df = df.set_index(IDX)
        new_frames.append(df)
        print("  %s 拉取 %d 行" % (d, len(df)))
    if not new_frames:
        print("无新数据写入")
        return
    new = pd.concat(new_frames)

    if os.path.exists(PARQUET):
        old = pd.read_parquet(PARQUET)
        merged = pd.concat([old, new])
        merged = merged[~merged.index.duplicated(keep="last")]
    else:
        merged = new
    merged = merged.sort_index()

    if os.path.exists(PARQUET):
        shutil.copy(PARQUET, PARQUET + ".bak")
    merged.to_parquet(PARQUET)
    new_latest = merged.index.get_level_values("trade_date").max().date()
    print("写入 %s（%d 行，最新 %s）" % (PARQUET, len(merged), new_latest))


if __name__ == "__main__":
    main()
