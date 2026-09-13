# -*- coding: utf-8 -*-
"""DE R3 Task B 拉数：全天候四腿+纳指腿（Tushare，代理口径）
- 股: 000300.SH index_daily（代理 510300）
- 债: 511010 国债ETF fund_daily + 000012.SH 上证国债指数（双代理候选）
- 金: 518880 黄金ETF fund_daily
- 货: 511990 华宝添益 + 511880 银华日利（货币腿候选，检查折算/除息跳变）
- 纳指: 513100 纳指ETF fund_daily
另拉 fund_basic 核对基金名称。
"""
import json
import os
import time
import urllib.request

API = "https://api.tushare.pro"
TOKEN = os.environ.get("TUSHARE_TOKEN", "")
OUT = os.path.dirname(os.path.abspath(__file__))


def ts(api_name, params=None, fields=""):
    body = {"api_name": api_name, "token": TOKEN, "params": params or {}}
    if fields:
        body["fields"] = fields
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    last_err = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8"))
            if res.get("code") != 0:
                raise RuntimeError("api %s err: %s" % (api_name, res.get("message")))
            d = res["data"]
            return [dict(zip(d["fields"], row)) for row in d["items"]]
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise last_err


def pull_daily(api, code, start="20140101", end="20260911", fl="trade_date,close,pre_close"):
    rows = ts(api, {"ts_code": code, "start_date": start, "end_date": end}, fl)
    rows = [r for r in rows if r.get("close")]
    rows.sort(key=lambda r: r["trade_date"])
    return rows


def main():
    result = {}

    fb = ts("fund_basic", {"ts_code": "511010.SH,518880.SH,511990.SH,511880.SH,513100.SH"},
            "ts_code,name,found_date,list_date,management,benchmark")
    result["fund_basic"] = fb
    for r in fb:
        print("fund: %s %s list=%s bench=%s" % (
            r["ts_code"], r["name"], r.get("list_date"), r.get("benchmark")))

    legs = {
        "stock_000300": ("index_daily", "000300.SH"),
        "bond_000012": ("index_daily", "000012.SH"),
        "bond_511010": ("fund_daily", "511010.SH"),
        "gold_518880": ("fund_daily", "518880.SH"),
        "money_511990": ("fund_daily", "511990.SH"),
        "money_511880": ("fund_daily", "511880.SH"),
        "nq_513100": ("fund_daily", "513100.SH"),
    }
    for key, (api, code) in legs.items():
        rows = pull_daily(api, code)
        result[key] = rows
        # 质检：close/pre_close 日收益跳变
        rets = []
        for i in range(1, len(rows)):
            pc = rows[i].get("pre_close")
            if pc:
                rets.append(rows[i]["close"] / pc - 1.0)
        rets.sort()
        n_bad = sum(1 for x in rets if abs(x) > 0.02)
        print("%s (%s): rows=%d %s~%s min_ret=%.4f max_ret=%.4f |ret|>2%%: %d" % (
            key, code, len(rows), rows[0]["trade_date"] if rows else "-",
            rows[-1]["trade_date"] if rows else "-",
            rets[0] if rets else 0, rets[-1] if rets else 0, n_bad))
        time.sleep(0.4)

    with open(os.path.join(OUT, "de_r3_aw_legs.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print("[SAVED] de_r3_aw_legs.json")


if __name__ == "__main__":
    main()
