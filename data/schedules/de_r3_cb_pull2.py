# -*- coding: utf-8 -*-
"""DE R3 Task A 拉数v2：全字段cb_basic / bond_cov转股价历史 / 强赎接口替代探测 /
存活券停更扫描 / 000832.CSI / D2溢价率抽样 / 岭南转债价格核验"""
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
    req = urllib.request.Request(
        API, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    last_err = None
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8"))
            if res.get("code") != 0:
                return {"code": res.get("code"), "msg": res.get("message"), "fields": [], "items": []}
            d = res["data"]
            return {"code": 0, "msg": None, "fields": list(d["fields"]), "items": d["items"]}
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    return {"code": -1, "msg": str(last_err), "fields": [], "items": []}


def dicts(r):
    return [dict(zip(r["fields"], row)) for row in r["items"]]


def main():
    result = {}

    # ---- 1. cb_basic 全字段 ----
    all_fields = ("ts_code,bond_full_name,bond_short_name,cb_type,cb_code,stk_code,"
                  "stk_short_name,maturity,par,issue_price,issue_size,remain_size,"
                  "value_date,maturity_date,rate_type,coupon_rate,add_rate,pay_per_year,"
                  "list_date,delist_date,exchange,conv_start_date,conv_end_date,"
                  "conv_stop_date,first_conv_price,conv_price,rate_clause,"
                  "issue_rating,newest_rating,put_clause,call_clause,reset_clause,"
                  "guarantee_type,maturity_call_price")
    r = ts("cb_basic", {}, all_fields)
    basic = dicts(r)
    print("cb_basic full fields rows=%d, fields=%s" % (len(basic), r["fields"]))
    result["cb_basic"] = basic

    # D4 条款覆盖（尽力项）
    n = len(basic)
    d4 = {}
    for f in ("put_clause", "call_clause", "reset_clause", "guarantee_type", "maturity_call_price"):
        d4[f] = sum(1 for b in basic if b.get(f) not in (None, "", ))
    result["d4_coverage"] = d4
    print("D4 coverage: %s / %d" % (json.dumps(d4), n))

    # D3 评级覆盖与差分
    ir = sum(1 for b in basic if b.get("issue_rating"))
    nr = sum(1 for b in basic if b.get("newest_rating"))
    diff = [{"ts_code": b["ts_code"], "name": b.get("bond_short_name"),
             "issue": b.get("issue_rating"), "newest": b.get("newest_rating"),
             "delist": b.get("delist_date")}
            for b in basic if b.get("issue_rating") and b.get("newest_rating")
            and b["issue_rating"] != b["newest_rating"]]
    result["d3"] = {"total": n, "issue_rating": ir, "newest_rating": nr,
                    "diff_count": len(diff), "diff_examples": diff[:25]}
    print("D3: total=%d issue=%d newest=%d diff=%d" % (n, ir, nr, len(diff)))
    for e in diff[:15]:
        print("   diff: %s %s issue=%s newest=%s delist=%s" % (
            e["ts_code"], e["name"], e["issue"], e["newest"], e["delist"]))

    # D7：alive 券与近期实际交易集合比对
    alive = [b for b in basic if not b.get("delist_date") and b.get("cb_type") == "CB"]
    r2 = ts("cb_daily", {"trade_date": "20260911"}, "ts_code")
    traded_now = set(x["ts_code"] for x in dicts(r2))
    alive_set = set(b["ts_code"] for b in alive)
    dead_alive = sorted(alive_set - traded_now)  # basic说存活但9/11无行情
    print("D7: alive(CB)=%d traded@20260911=%d alive_but_not_traded=%d" % (
        len(alive_set), len(traded_now), len(dead_alive)))
    # 逐个查它们的最后行情日
    d7_dead = {}
    for code in dead_alive:
        rd = ts("cb_daily", {"ts_code": code, "start_date": "20240101",
                             "end_date": "20260911"}, "trade_date,close")
        rows = dicts(rd)
        dates = sorted(x["trade_date"] for x in rows)
        d7_dead[code] = {"last": dates[-1] if dates else None, "rows": len(rows)}
        time.sleep(0.12)
    result["d7_alive_not_traded"] = d7_dead
    for k, v in list(d7_dead.items())[:40]:
        b = next((x for x in basic if x["ts_code"] == k), {})
        print("   dead_alive: %s %s last=%s" % (k, b.get("bond_short_name"), v["last"]))

    # 岭南转债价格核验（数据说 2026 仍在交易）
    rl = ts("cb_daily", {"ts_code": "128044.SZ", "start_date": "20240801",
                         "end_date": "20260911"}, "trade_date,close,vol,amount")
    rows_l = dicts(rl)
    if rows_l:
        by_month = {}
        for x in rows_l:
            by_month[x["trade_date"][:6]] = x["close"]
        result["lingnan_monthly_close"] = dict(sorted(by_month.items()))
        print("岭南转债月度收盘(抽样): %s" % json.dumps(
            dict(list(sorted(by_month.items()))[::6]), ensure_ascii=False))

    # ---- 2. bond_cov 转股价历史（下修轨迹，D2/D5 关联） ----
    rc = ts("bond_cov", {"ts_code": "113537.SH"})
    result["bond_cov_probe"] = {"code": rc["code"], "msg": rc["msg"],
                                "fields": rc["fields"],
                                "rows": len(rc["items"]),
                                "sample": rc["items"][:3]}
    print("bond_cov 113537.SH: code=%s rows=%d fields=%s" % (
        rc["code"], len(rc["items"]), rc["fields"]))

    # ---- 3. cb_call 替代接口探测 ----
    for api in ("cb_call", "bond_cb_redeem", "bond_redeem"):
        rp = ts(api, {"ts_code": "113537.SH"})
        result["probe_%s" % api] = {"code": rp["code"], "msg": rp["msg"],
                                    "fields": rp["fields"], "rows": len(rp["items"])}
        print("%s: code=%s msg=%s rows=%d fields=%s" % (
            api, rp["code"], rp["msg"], len(rp["items"]), rp["fields"]))
        time.sleep(0.3)

    # ---- 4. D8: 000832.CSI 中证转债指数 2019 起 ----
    r8 = ts("index_daily", {"ts_code": "000832.CSI", "start_date": "20190101",
                            "end_date": "20260911"}, "trade_date,close")
    idx = dicts(r8)
    result["d8_index"] = idx
    print("D8 000832.CSI rows=%d %s~%s" % (
        len(idx), idx[0]["trade_date"] if idx else "-", idx[-1]["trade_date"] if idx else "-"))

    # ---- 5. D2: 20 只活跃 CB × 最近 20 交易日，自算溢价率 ----
    import random
    random.seed(7)
    actives = [b for b in basic if b.get("cb_type") == "CB" and not b.get("delist_date")
               and b.get("conv_price") and b.get("stk_code")
               and b.get("list_date") and b["list_date"] <= "20240101"]
    picks = random.sample(actives, min(20, len(actives)))
    # 交易日历末 20 日
    rt = ts("trade_cal", {"exchange": "SSE", "start_date": "20260801",
                          "end_date": "20260911", "is_open": "1"}, "cal_date")
    cal20 = sorted(x["cal_date"] for x in dicts(rt))[-20:]
    d2 = {}
    for b in picks:
        code, stk = b["ts_code"], b["stk_code"]
        rb = ts("cb_daily", {"ts_code": code, "start_date": cal20[0], "end_date": cal20[-1]},
                "trade_date,close")
        rs = ts("daily", {"ts_code": stk, "start_date": cal20[0], "end_date": cal20[-1]},
                "trade_date,close")
        cb_close = {x["trade_date"]: x["close"] for x in dicts(rb) if x.get("close")}
        stk_close = {x["trade_date"]: x["close"] for x in dicts(rs) if x.get("close")}
        pairs = []
        for d in cal20:
            if d in cb_close and d in stk_close:
                cp = float(b["conv_price"])
                prem = float(cb_close[d]) / (float(stk_close[d]) / cp * 100.0) - 1.0
                pairs.append({"date": d, "cb": cb_close[d], "stk": stk_close[d],
                              "conv_value": round(float(stk_close[d]) / cp * 100.0, 2),
                              "prem": round(prem, 4)})
        d2[code] = {"name": b.get("bond_short_name"), "stk": stk,
                    "conv_price": b["conv_price"], "pairs": pairs}
        time.sleep(0.15)
    result["d2_premium_sample"] = d2
    tot = sum(len(v["pairs"]) for v in d2.values())
    print("D2: %d bonds, %d pairs" % (len(d2), tot))

    with open(os.path.join(OUT, "de_r3_cb_data2.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    print("[SAVED] de_r3_cb_data2.json")
    print("DONE pull2")


if __name__ == "__main__":
    main()
