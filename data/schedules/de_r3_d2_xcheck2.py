# -*- coding: utf-8 -*-
"""DE R3 D2 正股腿两源比对：Tushare daily vs 本地 astock parquet（权威源）
转债腿两源比对：Tushare cb_daily vs 东财 kline（轻量重试版）"""
import json
import os
import time
import urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def astock_check():
    import pyarrow.dataset as ds
    d2 = load("de_r3_cb_data2.json")["d2_premium_sample"]
    stks = list(set(v["stk"] for v in d2.values()))
    dataset = ds.dataset("D:/astock/daily/stock_daily.parquet", format="parquet")
    tbl = dataset.to_table(filter=(ds.field("trade_date") >= __import__("datetime").datetime(2026, 8, 1)))
    df = tbl.to_pandas()
    if "ts_code" not in df.columns:
        df = df.reset_index()
    a_close = {}
    for _, row in df[df["ts_code"].isin(stks)].iterrows():
        d = "%s" % row["trade_date"]
        d = d[:10].replace("-", "") if "-" in d else d[:8]
        a_close[(row["ts_code"], d)] = float(row["close"])
    print("astock 2026-08+ rows for sample stocks: %d" % len(a_close))
    diffs = []
    n_cmp = 0
    for code, v in d2.items():
        stk = v["stk"]
        for p in v["pairs"]:
            key = (stk, p["date"])
            if key in a_close:
                n_cmp += 1
                diffs.append(abs(a_close[key] - float(p["stk"])))
    res = {"n_cmp": n_cmp, "mean_absdiff": round(sum(diffs) / len(diffs), 5) if diffs else -1,
           "max_absdiff": round(max(diffs), 4) if diffs else -1,
           "n_exact": sum(1 for x in diffs if x < 1e-9)}
    print("astock vs tushare stock close: %s" % json.dumps(res))
    return res


def em_cb_check():
    d2 = load("de_r3_cb_data2.json")["d2_premium_sample"]
    diffs = []
    n_cmp = 0
    fails = []
    codes = list(d2.keys())
    for code in codes[:8]:
        v = d2[code]
        mkt = "1" if code.endswith(".SH") else "0"
        c6 = code.split(".")[0]
        url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s.%s"
               "&klt=101&fqt=0&beg=20260801&end=20260912&fields1=f1,f2,f3&fields2=f51,f53"
               % (mkt, c6))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://quote.eastmoney.com/"})
        data = None
        for i in range(2):
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    data = json.loads(r.read().decode("utf-8"))["data"]
                break
            except Exception:
                time.sleep(2)
        if not data or not data.get("klines"):
            fails.append(code)
            print("  fail: %s" % code)
            continue
        em = {}
        for k in data["klines"]:
            parts = k.split(",")
            em[parts[0].replace("-", "")] = float(parts[1])
        for p in v["pairs"]:
            if p["date"] in em:
                n_cmp += 1
                diffs.append(abs(em[p["date"]] - float(p["cb"])))
        time.sleep(4)
    res = {"n_cmp": n_cmp, "mean_absdiff": round(sum(diffs) / len(diffs), 5) if diffs else -1,
           "max_absdiff": round(max(diffs), 4) if diffs else -1,
           "n_exact": sum(1 for x in diffs if x < 1e-9),
           "fail_codes": fails}
    print("em vs tushare cb close: %s" % json.dumps(res, ensure_ascii=False))
    return res


if __name__ == "__main__":
    r1 = astock_check()
    r2 = em_cb_check()
    with open(os.path.join(OUT, "de_r3_d2_price_xcheck.json"), "w", encoding="utf-8") as f:
        json.dump({"stock_vs_astock": r1, "cb_vs_eastmoney": r2}, f, ensure_ascii=False, indent=1)
    print("[SAVED] de_r3_d2_price_xcheck.json")
