# -*- coding: utf-8 -*-
"""Project_19 可转债双低 - 数据管线。

拉取并落地（data/ 下）：
  cb_basic.parquet   转债基础全字段（评级/条款/转股价/余额/上市退市）
  cb_daily.parquet   转债日线（按券全历史，2019-01 起，含已退市券）
  cb_index.parquet   中证转债指数 000832.CSI
  trade_cal.parquet  SSE 交易日历
  em_cb_list.json    东财强赎/摘牌表（NOTICE_DATE/CEASE_DATE，全量快照）

正股日线用本地 D:/astock/daily/stock_daily.parquet（不复权 close + is_st），
回测阶段直接 join，不在本管线重复拉取。

用法：
  python research/cb_data.py              # 全量拉取
  python research/cb_data.py --limit 5    # 只拉前 5 只转债（联调用）
  python research/cb_data.py --skip-basic # 跳过已落地的表（断点续跑）
"""
import argparse
import json
import os
import sys
import time
import urllib.request

import pandas as pd

API = "https://api.tushare.pro"
TOKEN = os.environ.get("TUSHARE_TOKEN", "")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(HERE), "data")
EM_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))),
                      "data", "schedules", "de_r3_em_cb_list.json")

CB_BASIC_FIELDS = (
    "ts_code,bond_full_name,bond_short_name,cb_type,cb_code,stk_code,"
    "stk_short_name,maturity,par,issue_price,issue_size,remain_size,"
    "value_date,maturity_date,rate_type,coupon_rate,add_rate,pay_per_year,"
    "list_date,delist_date,exchange,conv_start_date,conv_end_date,"
    "conv_stop_date,first_conv_price,conv_price,rate_clause,"
    "issue_rating,newest_rating,put_clause,call_clause,reset_clause,"
    "guarantee_type,maturity_call_price"
)


def ts(api_name, params=None, fields=""):
    body = {"api_name": api_name, "token": TOKEN, "params": params or {}}
    if fields:
        body["fields"] = fields
    req = urllib.request.Request(
        API, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    last_err = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8"))
            if res.get("code") != 0:
                return {"code": res.get("code"), "msg": res.get("message"),
                        "fields": [], "items": []}
            d = res["data"]
            return {"code": 0, "msg": None,
                    "fields": list(d["fields"]), "items": d["items"]}
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (i + 1))
    return {"code": -1, "msg": str(last_err), "fields": [], "items": []}


def dicts(r):
    return [dict(zip(r["fields"], row)) for row in r["items"]]


def to_df(rows):
    return pd.DataFrame.from_records(rows) if rows else pd.DataFrame()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 只（联调）")
    ap.add_argument("--skip-basic", action="store_true")
    ap.add_argument("--start", default="20190101")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    print("[cb_data] data dir:", DATA_DIR)

    # ---- 1. cb_basic 全字段 ----
    basic_path = os.path.join(DATA_DIR, "cb_basic.parquet")
    if args.skip_basic and os.path.exists(basic_path):
        basic = pd.read_parquet(basic_path)
        print("[cb_basic] loaded from cache rows=%d" % len(basic))
    else:
        r = ts("cb_basic", {}, CB_BASIC_FIELDS)
        if r["code"] != 0:
            print("[FATAL] cb_basic fail: %s" % r["msg"])
            sys.exit(2)
        basic = to_df(dicts(r))
        basic.to_parquet(basic_path, index=False)
        print("[cb_basic] saved rows=%d" % len(basic))

    codes = basic["ts_code"].tolist()
    if args.limit:
        codes = codes[: args.limit]
        print("[cb_daily] LIMIT mode: %d bonds" % len(codes))

    # ---- 2. cb_daily 按券全历史 ----
    daily_path = os.path.join(DATA_DIR, "cb_daily.parquet")
    if os.path.exists(daily_path):
        daily = pd.read_parquet(daily_path)
        done = set(daily["ts_code"].unique())
        print("[cb_daily] cache rows=%d already=%d bonds" % (len(daily), len(done)))
    else:
        daily = pd.DataFrame()
        done = set()
    todo = [c for c in codes if c not in done]
    print("[cb_daily] to fetch: %d bonds" % len(todo))
    frames = [daily] if len(daily) else []
    for i, code in enumerate(todo):
        r = ts("cb_daily", {"ts_code": code, "start_date": args.start},
               "ts_code,trade_date,pre_close,open,high,low,close,change,pct_chg,vol,amount")
        rows = dicts(r)
        if rows:
            frames.append(to_df(rows))
        if (i + 1) % 50 == 0 or i == len(todo) - 1:
            print("[cb_daily] %d/%d" % (i + 1, len(todo)), flush=True)
            if len(frames) > 1:
                df_all = pd.concat(frames, ignore_index=True)
                df_all.to_parquet(daily_path, index=False)
                frames = [df_all]
        time.sleep(0.13)
    if frames:
        df_all = pd.concat(frames, ignore_index=True)
        df_all.to_parquet(daily_path, index=False)
        print("[cb_daily] saved rows=%d" % len(df_all))

    # ---- 3. 中证转债指数 ----
    idx_path = os.path.join(DATA_DIR, "cb_index.parquet")
    if not os.path.exists(idx_path):
        r = ts("index_daily", {"ts_code": "000832.CSI", "start_date": args.start},
               "trade_date,close")
        rows = dicts(r)
        if not rows:
            print("[FATAL] index empty")
            sys.exit(2)
        to_df(rows).to_parquet(idx_path, index=False)
        print("[cb_index] saved rows=%d" % len(rows))
    else:
        print("[cb_index] exists")

    # ---- 4. 交易日历 ----
    cal_path = os.path.join(DATA_DIR, "trade_cal.parquet")
    if not os.path.exists(cal_path):
        r = ts("trade_cal", {"exchange": "SSE", "start_date": args.start,
                             "is_open": "1"}, "cal_date")
        rows = dicts(r)
        to_df(rows).to_parquet(cal_path, index=False)
        print("[trade_cal] saved rows=%d" % len(rows))
    else:
        print("[trade_cal] exists")

    # ---- 5. 东财强赎/摘牌表 ----
    em_path = os.path.join(DATA_DIR, "em_cb_list.json")
    if os.path.exists(EM_SRC):
        with open(EM_SRC, "r", encoding="utf-8") as f:
            em = json.load(f)
        with open(em_path, "w", encoding="utf-8") as f:
            json.dump(em, f, ensure_ascii=False)
        print("[em_cb_list] copied rows=%d" % len(em))
    else:
        print("[WARN] em source not found:", EM_SRC)

    print("[cb_data] DONE")


if __name__ == "__main__":
    main()
