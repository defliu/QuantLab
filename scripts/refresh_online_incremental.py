# coding: utf-8
"""G2 周训数据在线增量补充（Tushare）——把 D:/astock 的 lhb/research/northbound 三表
增量刷新到最新，供 train_g2.py 周训使用（防离线包未及时下载导致训练面板断源退化）。

背景（2026-09-14 立，诚哥拍板"全做"）：
  G2 训练面板 10 个 g2 增强特征 = lhb_net/lhb_count(龙虎榜) + north_chg(北向)
  + rc_rating/rc_num(研报) + mf_*(资金流，已有独立脚本每日刷新) + ind_*(同花顺板块)。
  实测 Tushare token（2000 积分档，ACTIVE）：top_list/report_rc/hk_hold 三接口可用；
  ths_daily/moneyflow_ind_ths/ths_index 无权限（需 5000 档，诚哥拍板暂不升级，
  板块两特征继续靠 E 盘离线包）。

覆盖表（Tushare 字段与 D:/astock 本地 parquet 逐列一致，已实测核对 schema）：
  lhb/top_list.parquet               <- pro.top_list(trade_date)    按 ts_code 去重（多日累计偏离优先，对齐 D:/astock 口径）
  research/report_rc_daily.parquet   <- pro.report_rc(trade_date)   index=(ts_code,report_date,org_name,author_name,report_title,quarter)
  northbound/hk_hold_full.parquet    <- pro.hk_hold(trade_date)     日频（2026 实测已恢复；本地 trade_date 为 'YYYYMMDD' 字符串）

用法：
  python refresh_online_incremental.py                 # dry-run：只打印待刷区间与计划
  python refresh_online_incremental.py --apply         # 增量拉取 + 备份 + 合并写回 + 写后验证
  python refresh_online_incremental.py --apply --days 10
  python refresh_online_incremental.py --only lhb/top_list
"""
import argparse
import json
import os
import shutil
import sys
import time

import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KEY_FILE = "D:/QuantLab/config/data_source_keys.json"
ASTOCK = "D:/astock"

# 多日累计偏离关键词（与 g2_realtime.py _LHB_MULTI_DAYS_KW 完全一致，勿单边改）
LHB_MULTI_DAYS_KW = ("连续10个交易日", "连续三个交易日", "连续3个交易日", "连续3个交易日内")

# 表定义：index_cols=写回时设置的 MultiIndex；date_col=日期字段；
# date_is_str=本地日期是否为 'YYYYMMDD' 字符串（hk_hold 是，lhb/report_rc 是 Timestamp）
TABLES = [
    {
        "name": "lhb/top_list",
        "path": os.path.join(ASTOCK, "lhb", "top_list.parquet"),
        "api": "top_list",
        "index_cols": ["trade_date", "ts_code"],
        "date_col": "trade_date",
        "date_is_str": False,
        "dedup_lhb": True,
    },
    {
        "name": "research/report_rc_daily",
        "path": os.path.join(ASTOCK, "research", "report_rc_daily.parquet"),
        "api": "report_rc",
        "index_cols": ["ts_code", "report_date", "org_name", "author_name", "report_title", "quarter"],
        "date_col": "report_date",
        "date_is_str": False,
        "dedup_lhb": False,
    },
    {
        "name": "northbound/hk_hold_full",
        "path": os.path.join(ASTOCK, "northbound", "hk_hold_full.parquet"),
        "api": "hk_hold",
        "index_cols": None,          # 本地为 RangeIndex（普通列）
        "keys": ["code", "trade_date"],
        "col_order": ["code", "trade_date", "ts_code", "name", "vol", "ratio", "exchange"],
        "date_col": "trade_date",
        "date_is_str": True,
        "dedup_lhb": False,
    },
]


def load_token():
    with open(KEY_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    s = cfg.get("sources", {}).get("tushare", {})
    tk = s.get("token")
    if not tk or tk == "待填":
        raise SystemExit("tushare.token 未配置：请在 config/data_source_keys.json 填入后重试")
    return tk


def _ensure_tushare():
    try:
        import tushare as ts
        return ts
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "tushare"], check=True)
        import tushare as ts
        return ts


def trade_cal(pro, start, end):
    try:
        cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1", fields="cal_date")
        return cal["cal_date"].tolist()
    except Exception:
        return pd.bdate_range(start, end).strftime("%Y%m%d").tolist()


def _lhb_multi_rank(expl):
    for i, kw in enumerate(LHB_MULTI_DAYS_KW):
        if kw in expl:
            return i
    return None


def dedup_lhb(df):
    """按 ts_code 去重（对齐 D:/astock 实测口径：多原因上榜每股保留 1 行，多日累计偏离优先）。"""
    df = df.copy()
    df["_rank"] = df["reason"].map(_lhb_multi_rank)
    df = df.sort_values(["_rank"], na_position="last")
    out = df.groupby("ts_code", as_index=False).first()
    return out.drop(columns=["_rank"])


def _local_max(cur, table):
    """取本地最新日期（YYYYMMDD 字符串）。"""
    date_col = table["date_col"]
    if isinstance(cur.index, pd.MultiIndex) and date_col in cur.index.names:
        s = cur.index.get_level_values(date_col)
        if table["date_is_str"]:
            return str(s.max())
        return pd.Timestamp(s.max()).strftime("%Y%m%d")
    if date_col in cur.columns:
        s = cur[date_col]
        if table["date_is_str"]:
            return str(s.max())
        return pd.Timestamp(s.max()).strftime("%Y%m%d")
    raise RuntimeError("无法定位本地最新日期列 %s" % date_col)


def fetch_day(pro, api, date_str):
    fn = getattr(pro, api)
    if api == "report_rc":
        frames = []
        offset = 0
        while True:
            part = fn(trade_date=date_str, limit=5000, offset=offset)
            if part is None or part.empty:
                break
            frames.append(part)
            if len(part) < 5000:
                break
            offset += 5000
            time.sleep(361)  # report_rc 限频 10 次/小时（实测 2026-09-14），翻页需等待
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return fn(trade_date=date_str)


def refresh_table(pro, table, end_date, days, apply_mode):
    name = table["name"]
    path = table["path"]
    api = table["api"]
    st = dict(name=name, status="PENDING")

    if not os.path.isfile(path):
        raise RuntimeError("%s 不存在: %s" % (name, path))

    cur = pd.read_parquet(path)
    cur_max = _local_max(cur, table)
    st["local_max"] = cur_max
    print("  [%s] 本地最新 %s（%d 行）" % (name, cur_max, len(cur)))

    dates = [d for d in trade_cal(pro, cur_max, end_date) if d > cur_max]
    if not dates:
        st["status"] = "UP_TO_DATE"
        print("  [%s] 已最新，无需刷新" % name)
        return st
    dates = dates[-days:]
    st["days_to_fetch"] = len(dates)
    print("  [%s] 待刷 %d 个交易日：%s ... %s" % (name, len(dates), dates[0], dates[-1]))

    if not apply_mode:
        st["status"] = "PLAN_OK"
        return st

    new_frames = []
    for d in dates:
        df = fetch_day(pro, api, d)
        if df is None or df.empty:
            print("    %s 空，跳过" % d)
            continue
        if table.get("dedup_lhb"):
            before = len(df)
            df = dedup_lhb(df)
            print("    %s 拉取 %d 行（去重 %d→%d）" % (d, before, before, len(df)))
        else:
            print("    %s 拉取 %d 行" % (d, len(df)))
        new_frames.append(df)

    if not new_frames:
        st["status"] = "NO_NEW_DATA"
        print("  [%s] 无新数据写入" % name)
        return st

    new = pd.concat(new_frames, ignore_index=True)
    date_col = table["date_col"]
    if not table["date_is_str"]:
        new[date_col] = pd.to_datetime(new[date_col])

    # 按目标表结构合并
    if table["index_cols"]:
        new = new.set_index(table["index_cols"])
        if isinstance(cur.index, pd.MultiIndex) and cur.index.names == table["index_cols"]:
            pass
        else:
            cur = cur.reset_index()
            cur = cur.set_index([c for c in table["index_cols"] if c in cur.columns])
        merged = pd.concat([cur, new])
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.sort_index()
    else:
        keys = table["keys"]
        new = new.set_index(keys)
        cur = cur.set_index(keys) if not isinstance(cur.index, pd.MultiIndex) else cur
        merged = pd.concat([cur, new])
        merged = merged[~merged.index.duplicated(keep="last")]
        merged = merged.sort_index()
        # 恢复 RangeIndex（原表为普通列结构：trade_date 是列不是索引，
        # train_g2/build_g2_daily 用 hk["trade_date"] 列访问，不得改 schema）
        merged = merged.reset_index()
        if "col_order" in table:
            merged = merged[[c for c in table["col_order"] if c in merged.columns]]

    bak = path + ".bak_online"
    if os.path.exists(bak):
        os.remove(bak)
    shutil.copy2(path, bak)
    st["backup"] = bak
    merged.to_parquet(path)
    st["new_rows"] = len(merged)

    # 写后验证
    v = pd.read_parquet(path)
    v_max = _local_max(v, table)
    st["verified_max"] = v_max
    st["verified_rows"] = len(v)
    ok = (len(v) == len(merged)) and v_max >= dates[-1]
    st["status"] = "OK" if ok else "FAIL"
    if ok:
        print("  [%s] 写后复核 %d 行 / 最新 %s → OK" % (name, len(v), v_max))
    else:
        raise RuntimeError("%s 写后复核失败: %d 行 / 最新 %s（预期≥%s）" % (name, len(v), v_max, dates[-1]))
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际拉取+写回（默认 dry-run）")
    ap.add_argument("--days", type=int, default=30, help="回溯最多 N 个交易日（默认 30）")
    ap.add_argument("--only", choices=[t["name"] for t in TABLES], default=None, help="只刷指定表")
    args = ap.parse_args()

    end_date = pd.Timestamp.now().strftime("%Y%m%d")
    pro = _ensure_tushare().pro_api(load_token())
    print("模式: %s | 目标最新 %s | 回溯 %d 交易日" % (
        "APPLY" if args.apply else "DRY-RUN（不写盘）", end_date, args.days))

    results = []
    for t in TABLES:
        if args.only and t["name"] != args.only:
            continue
        try:
            results.append(refresh_table(pro, t, end_date, args.days, args.apply))
        except Exception as e:
            print("  [%s] 失败: %s" % (t["name"], str(e)))
            results.append(dict(name=t["name"], status="ERROR", error=str(e)))
            # 单表失败不中断其余表（各表独立增量，互不依赖）

    print("\n===== 汇总 =====")
    for r in results:
        print("  %-28s %s" % (r["name"], r.get("status", "?")))
    sys.exit(0)


if __name__ == "__main__":
    main()
