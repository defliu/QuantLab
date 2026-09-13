# -*- coding: utf-8 -*-
"""DE 数据源权限实测：D(股东户数)/E(解禁大宗)/A(转债补充) 关键接口是否有权限。
只读：仅调用 Tushare API，不写任何文件，不打印 token。"""
import json
import os
import time
import urllib.request

API = "https://api.tushare.pro"
TOKEN = os.environ.get("TUSHARE_TOKEN", "")

TESTS = [
    ("stk_holdernumber", {"ts_code": "000001.SZ"}, "D股东户数"),
    ("share_float", {"ts_code": "000001.SZ"}, "E限售解禁"),
    ("block_trade", {"ts_code": "000001.SZ"}, "E大宗交易"),
    ("cb_basic", {"fields": "ts_code,bond_short_name,newest_rating,issue_rating,remain_size,conv_price,delist_date"}, "A转债复测"),
    ("top10_holders", {"ts_code": "000001.SZ", "period": "20240331"}, "D十大股东"),
    ("cb_daily", {"ts_code": "123108.SZ", "start_date": "20260801", "end_date": "20260911"}, "A转债行情复测"),
]


def call(api_name, params):
    body = json.dumps({"api_name": api_name, "token": TOKEN, "params": params}).encode("utf-8")
    req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        code = resp.get("code")
        if code == 0:
            d = resp.get("data") or {}
            items = d.get("items") or []
            n = len(items)
            sample = items[0][:6] if items else []
            return f"OK rows={n} sample={sample}"
        else:
            return f"ERR code={code} msg={str(resp.get('msg'))[:60]}"
    except Exception as e:
        return f"EXC {str(e)[:60]}"


for name, params, label in TESTS:
    print(f"[{label}] {name}: {call(name, params)}")
    time.sleep(1.1)
