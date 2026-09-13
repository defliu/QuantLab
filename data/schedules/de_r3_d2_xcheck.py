# -*- coding: utf-8 -*-
"""DE R3 D2/D9 终验：20券×20日 转债收盘 + 正股收盘 两源比对
Tushare(cb_daily/daily) vs 东方财富(push2his kline)"""
import json
import os
import time
import urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def em_kline(secid, beg, end):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&klt=101&fqt=0&beg=%s&end=%s&fields1=f1,f2,f3&fields2=f51,f53"
           % (secid, beg, end))
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://quote.eastmoney.com/"})
    res = None
    for i in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode("utf-8"))
            break
        except Exception:
            time.sleep(1.5 * (i + 1))
    if res is None:
        print("  [WARN] kline failed: %s" % secid)
        return {}
    out = {}
    for k in (res.get("data") or {}).get("klines", []):
        parts = k.split(",")
        out[parts[0].replace("-", "")] = float(parts[1])
    return out


def main():
    d2 = load("de_r3_cb_data2.json")["d2_premium_sample"]
    diffs_cb, diffs_stk = [], []
    detail = []
    for code, v in d2.items():
        mkt = "1" if code.endswith(".SH") else "0"
        c6 = code.split(".")[0]
        # 转债两源
        em_cb = em_kline("%s.%s" % (mkt, c6), "20260801", "20260912")
        for p in v["pairs"]:
            d = p["date"]
            if d in em_cb:
                diff = abs(em_cb[d] - float(p["cb"]))
                diffs_cb.append(diff)
        time.sleep(0.3)
        # 正股两源（不复权 fqt=0 vs tushare daily 不复权）
        stk = v["stk"]
        mkt_s = "1" if stk.endswith(".SH") else "0"
        s6 = stk.split(".")[0]
        em_stk = em_kline("%s.%s" % (mkt_s, s6), "20260801", "20260912")
        for p in v["pairs"]:
            d = p["date"]
            if d in em_stk:
                diff = abs(em_stk[d] - float(p["stk"]))
                diffs_stk.append(diff)
        time.sleep(0.3)
        detail.append({"code": code, "name": v["name"], "n_pairs": len(v["pairs"])})

    n = len(diffs_cb)
    mean_cb = sum(diffs_cb) / n if n else -1
    max_cb = max(diffs_cb) if n else -1
    n2 = len(diffs_stk)
    mean_stk = sum(diffs_stk) / n2 if n2 else -1
    max_stk = max(diffs_stk) if n2 else -1
    within5bp = sum(1 for x in diffs_cb if x / 100 <= 0.005)
    res = {"cb_pairs": n, "cb_mean_absdiff": round(mean_cb, 4),
           "cb_max_absdiff": round(max_cb, 4),
           "cb_within_0.5pct_price": within5bp,
           "stk_pairs": n2, "stk_mean_absdiff": round(mean_stk, 4),
           "stk_max_absdiff": round(max_stk, 4)}
    print(json.dumps(res, ensure_ascii=False))
    with open(os.path.join(OUT, "de_r3_d2_price_xcheck.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("[SAVED] de_r3_d2_price_xcheck.json")


if __name__ == "__main__":
    main()
