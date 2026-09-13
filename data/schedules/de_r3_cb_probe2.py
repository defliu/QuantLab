# -*- coding: utf-8 -*-
"""DE R3 探针2：排查 cb_basic 评级字段 / cb_call / 000832 指数 / cb_daily 最新日期"""
import json
import os
import time
import urllib.request

API = "https://api.tushare.pro"
TOKEN = os.environ.get("TUSHARE_TOKEN", "")


def raw(api_name, params=None, fields=""):
    body = {"api_name": api_name, "token": TOKEN, "params": params or {}}
    if fields:
        body["fields"] = fields
    req = urllib.request.Request(
        API, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


# 1. cb_basic 显式字段（评级/条款）
res = raw("cb_basic", {}, "ts_code,issue_rating,newest_rating,put_clause,call_clause,"
                          "reset_clause,guarantee_type,maturity_call_price,delist_type")
print("== cb_basic explicit fields: code=%s msg=%s" % (res.get("code"), res.get("message")))
if res.get("code") == 0 and res["data"]["items"]:
    print("   fields:", res["data"]["fields"])
    print("   rows:", len(res["data"]["items"]))
    print("   sample:", res["data"]["items"][0])

# 2. cb_call 原始错误
res = raw("cb_call", {})
print("== cb_call no-param: code=%s msg=%s" % (res.get("code"), res.get("message")))
if res.get("code") == 0:
    print("   fields:", res["data"]["fields"], "rows:", len(res["data"]["items"]))
    print("   sample:", res["data"]["items"][0] if res["data"]["items"] else None)

res = raw("cb_call", {"ts_code": "113537.SH"})
print("== cb_call by ts_code: code=%s msg=%s" % (res.get("code"), res.get("message")))
if res.get("code") == 0:
    print("   fields:", res["data"]["fields"], "rows:", len(res["data"]["items"]))

# 3. 000832 各种写法
for code in ("000832.SH", "000832.CSI", "sh000832"):
    res = raw("index_daily", {"ts_code": code, "start_date": "20240101", "end_date": "20240201"})
    print("== index_daily %s: code=%s msg=%s rows=%s" % (
        code, res.get("code"), res.get("message"),
        len(res["data"]["items"]) if res.get("code") == 0 else "-"))
    time.sleep(0.3)
# 换 index_daily_basic / 用 trade_date
res = raw("index_daily", {"trade_date": "20240115"})
print("== index_daily trade_date=20240115: code=%s msg=%s rows=%s" % (
    res.get("code"), res.get("message"),
    len(res["data"]["items"]) if res.get("code") == 0 else "-"))
if res.get("code") == 0 and res["data"]["items"]:
    codes832 = [r[0] for r in res["data"]["items"] if "832" in str(r[0])]
    print("   832 candidates:", codes832)
    print("   sample codes:", [r[0] for r in res["data"]["items"][:10]])

# 4. cb_daily 最新可用日期
for d in ("20260911", "20260910", "20260901", "20260821", "20260715"):
    res = raw("cb_daily", {"trade_date": d}, "ts_code")
    n = len(res["data"]["items"]) if res.get("code") == 0 else -1
    print("== cb_daily %s rows=%s" % (d, n))
    time.sleep(0.3)

# 5. 岭南转债 delist 情况
res = raw("cb_basic", {"ts_code": "128044.SZ"},
          "ts_code,bond_short_name,list_date,delist_date,delist_type,cb_type,exchange")
print("== 128044.SZ:", json.dumps(res["data"]["items"] if res.get("code") == 0 else res.get("message")))

# 6. cb_type 分布（用全字段拉一遍存盘备用）
res = raw("cb_basic")
if res.get("code") == 0:
    types = {}
    for r in res["data"]["items"]:
        t = r[res["data"]["fields"].index("cb_type")]
        types[t] = types.get(t, 0) + 1
    print("== cb_type dist:", json.dumps(types, ensure_ascii=False))
print("PROBE2 DONE")
