# -*- coding: utf-8 -*-
"""DE R3 Task A 交叉验证：东方财富公开接口（RPT_BOND_CB_LIST）vs Tushare
- D7: 摘牌日期两源比对
- D5: 强赎公告记录（NOTICE_DATE + EXECUTE_REASON）独立重建 2019-2026
- D2: 活跃券转股溢价率两源比对（Tushare 自算 vs 东财快照）
"""
import json
import os
import time
import urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def em_pull(page, size=500):
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?"
           "reportName=RPT_BOND_CB_LIST&columns=SECURITY_CODE,SECUCODE,"
           "SECURITY_NAME_ABBR,LISTING_DATE,DELIST_DATE,CEASE_DATE,EXPIRE_DATE,"
           "NOTICE_DATE_SH,NOTICE_DATE_HS,EXECUTE_REASON_SH,EXECUTE_REASON_HS,"
           "REDEEM_TYPE,RATING,IS_REDEEM,TRANSFER_PREMIUM_RATIO,"
           "CURRENT_BOND_PRICENEW,CONVERT_STOCK_PRICEHQ,TRANSFER_PRICE"
           "&pageSize=%d&pageNumber=%d" % (size, page))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode("utf-8"))
    if not res.get("success"):
        raise RuntimeError(res.get("message"))
    return res["result"]["data"], res["result"]["pages"]


def main():
    rows = []
    page = 1
    while True:
        data, pages = em_pull(page)
        rows.extend(data)
        if page >= pages:
            break
        page += 1
        time.sleep(0.5)
    print("eastmoney CB rows: %d" % len(rows))
    with open(os.path.join(OUT, "de_r3_em_cb_list.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)

    d2 = load("de_r3_cb_data2.json")
    basic = d2["cb_basic"]
    ts_by_code6 = {}
    for b in basic:
        c6 = b["ts_code"].split(".")[0]
        ts_by_code6[c6] = b

    # ---- D7: 摘牌日期比对 ----
    em_delisted = {}
    em_alive = set()
    for r in rows:
        c6 = r["SECURITY_CODE"]
        if r.get("DELIST_DATE"):
            em_delisted[c6] = r["DELIST_DATE"][:10].replace("-", "")
        else:
            em_alive.add(c6)
    ts_delisted = {b["ts_code"].split(".")[0]: b["delist_date"] for b in basic
                   if b.get("delist_date")}
    ts_alive = set(b["ts_code"].split(".")[0] for b in basic if not b.get("delist_date"))
    both_d = set(ts_delisted) & set(em_delisted)
    mismatch = [(c, ts_delisted[c], em_delisted[c]) for c in both_d
                if ts_delisted[c] != em_delisted[c]]
    only_ts_d = set(ts_delisted) - set(em_delisted)
    only_em_d = set(em_delisted) - set(ts_delisted)
    print("D7 xcheck: both_delisted=%d mismatch=%d only_tushare=%d only_em=%d" % (
        len(both_d), len(mismatch), len(only_ts_d), len(only_em_d)))
    print("   mismatch sample: %s" % mismatch[:10])
    # tushare 说存活、东财说摘牌 → tushare 缺 delist 记录（幸存者偏差风险）
    alive_in_ts_dead_in_em = sorted(ts_alive & set(em_delisted))
    print("   ts_alive_but_em_delisted: %s" % alive_in_ts_dead_in_em)
    rep_d7 = {"both": len(both_d), "mismatch": mismatch[:20],
              "mismatch_count": len(mismatch),
              "ts_alive_but_em_delisted": [
                  {"code": c, "em_delist": em_delisted[c],
                   "name": (ts_by_code6.get(c) or {}).get("bond_short_name")}
                  for c in alive_in_ts_dead_in_em],
              "only_tushare_delisted": sorted(only_ts_d),
              "only_em_delisted": sorted(only_em_d)}

    # ---- D5: 强赎公告重建（NOTICE_DATE 2019 起） ----
    redeem = []
    for r in rows:
        nd = r.get("NOTICE_DATE_SH") or r.get("NOTICE_DATE_HS")
        reason = r.get("EXECUTE_REASON_SH") or r.get("EXECUTE_REASON_HS")
        if not nd:
            continue
        nd8 = nd[:10].replace("-", "")
        if nd8 < "20190101":
            continue
        redeem.append({"code": r["SECURITY_CODE"], "name": r["SECURITY_NAME_ABBR"],
                       "notice": nd8, "reason": reason,
                       "delist": (r.get("DELIST_DATE") or "")[:10].replace("-", "")})
    by_year = {}
    by_reason = {}
    for r in redeem:
        y = r["notice"][:4]
        by_year[y] = by_year.get(y, 0) + 1
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
    print("D5 xcheck: em redeem notices 2019+ = %d by_year=%s by_reason=%s" % (
        len(redeem), json.dumps(dict(sorted(by_year.items()))), json.dumps(by_reason)))
    rep_d5 = {"count": len(redeem), "by_year": dict(sorted(by_year.items())),
              "by_reason": by_reason, "records": redeem}

    # ---- D2: 溢价率两源比对（活跃券） ----
    d2s = d2["d2_premium_sample"]
    em_prem = {}
    for r in rows:
        if r.get("TRANSFER_PREMIUM_RATIO") is not None and not r.get("DELIST_DATE"):
            em_prem[r["SECURITY_CODE"]] = {
                "prem": r["TRANSFER_PREMIUM_RATIO"],
                "cb_price": r.get("CURRENT_BOND_PRICENEW"),
                "stk_price": r.get("CONVERT_STOCK_PRICEHQ"),
                "conv_price": r.get("TRANSFER_PRICE")}
    comps = []
    for code, v in d2s.items():
        c6 = code.split(".")[0]
        if c6 not in em_prem:
            continue
        last = v["pairs"][-1] if v["pairs"] else None
        if not last:
            continue
        e = em_prem[c6]
        comps.append({"code": code, "name": v["name"],
                      "ts_date": last["date"], "ts_prem": last["prem"],
                      "em_prem_raw": e["prem"],
                      "em_cb_price": e["cb_price"], "ts_cb": last["cb"],
                      "em_stk": e["stk_price"], "ts_stk": last["stk"],
                      "conv_price": v["conv_price"]})
    for c in comps:
        # 东财 premium 单位试探：若 em_prem ~ ts_prem*100 则为百分数
        pass
    print("D2 xcheck: %d bonds comparable" % len(comps))
    for c in comps[:25]:
        print("   %s %s ts_prem=%.4f em_prem=%s (cb %s vs %s, stk %s vs %s)" % (
            c["code"], c["name"], c["ts_prem"], c["em_prem_raw"],
            c["em_cb_price"], c["ts_cb"], c["em_stk"], c["ts_stk"]))
    rep_d2 = comps

    result = {"d7_xcheck": rep_d7, "d5_xcheck": rep_d5, "d2_xcheck": rep_d2}
    with open(os.path.join(OUT, "de_r3_cb_xcheck.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("[SAVED] de_r3_cb_xcheck.json")


if __name__ == "__main__":
    main()
