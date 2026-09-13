# -*- coding: utf-8 -*-
"""DE R3 Task A: 转债数据源实锤验证 - 数据拉取（只读 Tushare，token 不落盘不打印）"""
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
                raise RuntimeError("api err: %s" % res.get("message"))
            d = res["data"]
            return d["fields"], d["items"]
        except Exception as e:
            last_err = e
            time.sleep(2 * (i + 1))
    raise last_err


def as_dicts(fields, items):
    return [dict(zip(fields, row)) for row in items]


def save(name, obj):
    p = os.path.join(OUT, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    print("[SAVED] %s" % name)


def main():
    result = {}

    # ---- 0. cb_basic 全量 ----
    fields, items = ts("cb_basic")
    basic = as_dicts(fields, items)
    print("cb_basic fields: %s" % fields)
    print("cb_basic rows: %d" % len(basic))
    result["cb_basic_fields"] = list(fields)
    result["cb_basic"] = basic

    # ---- 交易日历 2019-01-01 ~ 2026-09-11 ----
    fields, items = ts("trade_cal", {"exchange": "SSE", "start_date": "20190101",
                                     "end_date": "20260911", "is_open": "1"}, "cal_date")
    cal = [r[0] for r in items]
    print("trade_cal days 2019-2026: %d (%s ~ %s)" % (len(cal), cal[0], cal[-1]))
    result["cal"] = cal

    # ---- cb_daily 字段探测（单券单日 + 全字段） ----
    f2, it2 = ts("cb_daily", {"ts_code": "113537.SH", "trade_date": "20210820"})
    print("cb_daily fields: %s" % f2)
    result["cb_daily_fields"] = list(f2)
    result["cb_daily_sample"] = as_dicts(f2, it2)

    # ---- D1a: 全市场逐日覆盖（每年抽 3 个日期） ----
    probe_dates = []
    for y in range(2019, 2027):
        for md in ("0408", "0715", "1022"):
            d = "%d%s" % (y, md)
            if d in cal:
                probe_dates.append(d)
    d1a = {}
    for d in probe_dates:
        fd, itd = ts("cb_daily", {"trade_date": d}, "ts_code")
        d1a[d] = len(itd)
        time.sleep(0.25)
    result["d1_market_count"] = d1a
    print("D1a market counts: %s" % json.dumps(d1a))

    # cb_basic 内 list_date/delist_date 口径的当日存续数
    def listed_on(d):
        d8 = d
        n = 0
        for b in basic:
            if not b.get("list_date"):
                continue
            if b["list_date"] > d8:
                continue
            dl = b.get("delist_date")
            if dl and dl < d8:
                continue
            n += 1
        return n
    d1b = {d: listed_on(d) for d in probe_dates}
    result["d1_basic_listed_count"] = d1b
    print("D1b basic-listed counts: %s" % json.dumps(d1b))

    # ---- D1b: ≥10 只存续老券逐日抽验（2018 前上市、存续期跨 2019） ----
    olds = [b for b in basic if b.get("list_date") and b["list_date"] <= "20181231"
            and (not b.get("delist_date") or b["delist_date"] >= "20200101")]
    olds.sort(key=lambda b: b.get("delist_date") or "99999999", reverse=True)
    picks = olds[:12]
    print("old-bond pool: %d, picked %d" % (len(olds), len(picks)))
    d1_old = {}
    for b in picks:
        code = b["ts_code"]
        fd, itc = ts("cb_daily", {"ts_code": code, "start_date": "20190101",
                                  "end_date": "20260911"})
        rows = as_dicts(fd, itc)
        dates = sorted(r["trade_date"] for r in rows if r.get("trade_date"))
        d1_old[code] = {
            "name": b.get("bond_short_name"),
            "list_date": b.get("list_date"),
            "delist_date": b.get("delist_date"),
            "rows": len(rows),
            "first": dates[0] if dates else None,
            "last": dates[-1] if dates else None,
        }
        # 存续窗口内应交易日数
        lo = max("20190101", b["list_date"])
        hi = min("20260911", b.get("delist_date") or "20260911")
        expect = [d for d in cal if lo <= d <= hi]
        d1_old[code]["expect_days"] = len(expect)
        d1_old[code]["coverage"] = round(len(rows) / len(expect), 4) if expect else None
        time.sleep(0.25)
    result["d1_old_bonds"] = d1_old
    for k, v in d1_old.items():
        print("D1 old %s %s rows=%d/%s cov=%s (%s~%s)" % (
            k, v["name"], v["rows"], v["expect_days"], v["coverage"], v["first"], v["last"]))

    # ---- D2: 溢价率两口径抽样（若 cb_daily 自带溢价字段） ----
    d2_cands = [b for b in basic if b.get("list_date") and b["list_date"] <= "20200101"
                and (not b.get("delist_date") or b["delist_date"] >= "20260901")
                and b.get("conv_price") and b.get("stk_code")]
    import random
    random.seed(42)
    d2_picks = random.sample(d2_cands, min(20, len(d2_cands)))
    d2_data = {}
    has_prem_field = any("prem" in f.lower() or "溢价" in f for f in result["cb_daily_fields"])
    result["d2_cb_daily_has_premium_field"] = has_prem_field
    recent_dates = cal[-20:]
    for b in d2_picks:
        code = b["ts_code"]
        stk = b["stk_code"]
        fd, itc = ts("cb_daily", {"ts_code": code, "start_date": recent_dates[0],
                                  "end_date": recent_dates[-1]})
        cb_rows = [r for r in as_dicts(fd, itc) if r.get("close")]
        fd2, its = ts("daily", {"ts_code": stk, "start_date": recent_dates[0],
                                "end_date": recent_dates[-1]}, "trade_date,close")
        stk_close = {r["trade_date"]: r["close"] for r in as_dicts(fd2, its) if r.get("close")}
        pairs = []
        for r in cb_rows:
            sc = stk_close.get(r["trade_date"])
            if not sc or not b.get("conv_price"):
                continue
            conv_price = float(b["conv_price"])
            if conv_price <= 0:
                continue
            prem_calc = float(r["close"]) / (float(sc) / conv_price * 100.0) - 1.0
            row = {"date": r["trade_date"], "cb_close": r["close"],
                   "stk_close": sc, "prem_calc": round(prem_calc, 4)}
            if has_prem_field:
                for f in result["cb_daily_fields"]:
                    if "prem" in f.lower():
                        row["prem_field"] = r.get(f)
            pairs.append(row)
        d2_data[code] = {"name": b.get("bond_short_name"), "stk_code": stk,
                         "conv_price": b.get("conv_price"), "pairs": pairs}
        time.sleep(0.25)
    result["d2_premium_sample"] = d2_data
    n_pairs = sum(len(v["pairs"]) for v in d2_data.values())
    print("D2 sample: %d bonds, %d pairs, cb_daily_has_premium_field=%s" % (
        len(d2_data), n_pairs, has_prem_field))

    # ---- D3: 评级字段覆盖与快照性质 ----
    n = len(basic)
    ir = sum(1 for b in basic if b.get("issue_rating"))
    nr = sum(1 for b in basic if b.get("newest_rating"))
    result["d3_coverage"] = {"total": n, "issue_rating": ir, "newest_rating": nr}
    diff = [b for b in basic if b.get("issue_rating") and b.get("newest_rating")
            and b["issue_rating"] != b["newest_rating"]]
    result["d3_diff_count"] = len(diff)
    result["d3_diff_examples"] = [
        {"ts_code": b["ts_code"], "name": b.get("bond_short_name"),
         "issue": b.get("issue_rating"), "newest": b.get("newest_rating")}
        for b in diff[:20]]
    # 低评级/违约券例子（搜特/蓝盾/鸿达等）
    suspects = [b for b in basic if b.get("newest_rating") and b["newest_rating"] in
                ("A+", "A", "A-1", "BBB+", "BBB", "BB", "B", "C", "CC")]
    result["d3_low_rating_examples"] = [
        {"ts_code": b["ts_code"], "name": b.get("bond_short_name"),
         "issue": b.get("issue_rating"), "newest": b.get("newest_rating"),
         "delist_date": b.get("delist_date")} for b in suspects[:20]]
    print("D3 coverage: total=%d issue=%d newest=%d diff=%d low_rating=%d" % (
        n, ir, nr, len(diff), len(suspects)))

    # ---- D5: cb_call 强赎记录 ----
    try:
        fc, itcall = ts("cb_call")
        calls = as_dicts(fc, itcall)
        result["cb_call_fields"] = list(fc)
        result["cb_call"] = calls
        print("cb_call fields: %s rows: %d" % (fc, len(calls)))
    except Exception as e:
        print("cb_call FAILED: %s" % e)
        result["cb_call_error"] = str(e)
        calls = []

    # ---- D7: 摘牌统计 ----
    delisted = [b for b in basic if b.get("delist_date")]
    alive = [b for b in basic if not b.get("delist_date")]
    result["d7"] = {"total": len(basic), "delisted": len(delisted), "alive": len(alive)}
    dl_years = {}
    for b in delisted:
        y = b["delist_date"][:4]
        dl_years[y] = dl_years.get(y, 0) + 1
    result["d7_delist_by_year"] = dl_years
    print("D7: total=%d delisted=%d alive=%d by_year=%s" % (
        len(basic), len(delisted), len(alive), json.dumps(dl_years, ensure_ascii=False)))

    # ---- D8: 中证转债指数 ----
    try:
        fi, itidx = ts("index_daily", {"ts_code": "000832.SH", "start_date": "20190101",
                                       "end_date": "20260911"}, "trade_date,close")
        idx = as_dicts(fi, itidx)
        result["d8_index_rows"] = len(idx)
        result["d8_index_first"] = idx[0]["trade_date"] if idx else None
        result["d8_index_last"] = idx[-1]["trade_date"] if idx else None
        # 连续性检查
        idx_dates = set(r["trade_date"] for r in idx)
        missing = [d for d in cal if d not in idx_dates]
        result["d8_missing_days"] = len(missing)
        print("D8 000832.SH: rows=%d %s~%s missing_vs_cal=%d" % (
            len(idx), result["d8_index_first"], result["d8_index_last"], len(missing)))
    except Exception as e:
        print("D8 FAILED: %s" % e)
        result["d8_error"] = str(e)

    save("de_r3_cb_data.json", result)
    print("DONE Task A pull")


if __name__ == "__main__":
    main()
