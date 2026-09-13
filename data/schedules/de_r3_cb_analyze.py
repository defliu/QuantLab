# -*- coding: utf-8 -*-
"""DE R3 Task A 分析：D1-D8 汇总 + 岭南转债量能核验 + 强赎代理推断 + D2 溢价率统计"""
import json
import os
import urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))
API = "https://api.tushare.pro"
TOKEN = os.environ.get("TUSHARE_TOKEN", "")


def ts(api_name, params=None, fields=""):
    body = {"api_name": api_name, "token": TOKEN, "params": params or {}}
    if fields:
        body["fields"] = fields
    req = urllib.request.Request(API, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    for i in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8"))
            if res.get("code") != 0:
                raise RuntimeError(res.get("message"))
            return [dict(zip(res["data"]["fields"], row)) for row in res["data"]["items"]]
        except Exception:
            if i == 2:
                raise
            import time
            time.sleep(2)


def load(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def main():
    d1 = load("de_r3_cb_data.json")
    d2 = load("de_r3_cb_data2.json")
    rep = {}

    # 岭南转债 2025-2026 量能核验（是否僵尸行情）
    ln = ts("cb_daily", {"ts_code": "128044.SZ", "start_date": "20250101",
                         "end_date": "20260911"}, "trade_date,close,vol,amount")
    ln = [x for x in ln if x.get("trade_date")]
    ln.sort(key=lambda x: x["trade_date"])
    nonzero = [x for x in ln if (x.get("vol") or 0) > 0]
    frozen = {"last_nonzero": nonzero[-1]["trade_date"] if nonzero else None,
              "last_row": ln[-1]["trade_date"] if ln else None,
              "rows": len(ln), "nonzero_rows": len(nonzero)}
    rep["lingnan"] = frozen
    print("岭南转债: %s" % json.dumps(frozen))

    # D1 汇总
    old = d1["d1_old_bonds"]
    real = {k: v for k, v in old.items() if v["rows"] > 0}
    covs = sorted(v["coverage"] for v in real.values())
    rep["d1_old"] = {"n": len(real), "min_cov": covs[0], "median_cov": covs[len(covs) // 2],
                     "detail": old}
    mc = d1["d1_market_count"]
    bc = d1["d1_basic_listed_count"]
    ratios = {d: round(mc[d] / bc[d], 4) for d in mc}
    rep["d1_market_vs_basic"] = ratios
    print("D1 old: n=%d min_cov=%s med=%s" % (len(real), covs[0], covs[len(covs) // 2]))
    print("D1 mkt/basic ratios: %s" % json.dumps(ratios))

    # D2 溢价率统计
    prem_all = []
    per_bond = {}
    for code, v in d2["d2_premium_sample"].items():
        ps = [p["prem"] for p in v["pairs"]]
        if ps:
            per_bond[code] = {"name": v["name"], "n": len(ps),
                              "mean": round(sum(ps) / len(ps), 4),
                              "min": round(min(ps), 4), "max": round(max(ps), 4)}
            prem_all.extend(ps)
    prem_all.sort()
    rep["d2_stats"] = {
        "n_bonds": len(per_bond), "n_obs": len(prem_all),
        "mean": round(sum(prem_all) / len(prem_all), 4),
        "min": round(prem_all[0], 4), "p5": round(prem_all[len(prem_all) // 20], 4),
        "median": round(prem_all[len(prem_all) // 2], 4),
        "p95": round(prem_all[-len(prem_all) // 20], 4), "max": round(prem_all[-1], 4),
        "per_bond": per_bond}
    print("D2 prem stats: %s" % json.dumps({k: v for k, v in rep["d2_stats"].items() if k != "per_bond"}))

    # D5 代理：早摘牌（delist << maturity）推断强赎
    basic = d2["cb_basic"]
    early, at_mat, unknown = [], [], []
    for b in basic:
        if not b.get("delist_date"):
            continue
        md = b.get("maturity_date")
        if not md:
            unknown.append(b["ts_code"])
            continue
        gap_months = (int(md[:4]) - int(b["delist_date"][:4])) * 12 + \
                     (int(md[4:6]) - int(b["delist_date"][4:6]))
        if gap_months >= 6:
            early.append(b["ts_code"])
        else:
            at_mat.append(b["ts_code"])
    by_year_early = {}
    for b in basic:
        if b["ts_code"] in set(early):
            y = b["delist_date"][:4]
            by_year_early[y] = by_year_early.get(y, 0) + 1
    rep["d5_proxy"] = {"early_delist": len(early), "at_maturity": len(at_mat),
                       "unknown": len(unknown),
                       "early_by_year": dict(sorted(by_year_early.items()))}
    print("D5 proxy: early=%d at_mat=%d unknown=%d by_year=%s" % (
        len(early), len(at_mat), len(unknown), json.dumps(rep["d5_proxy"]["early_by_year"])))

    # 知名强赎券核对（公开常识）
    known = {"平银转债": "2019-05", "英科转债": "2021", "中鼎转2": "2020",
             "桃李转债": "2020", "盘龙转债": "2022"}
    checks = []
    for b in basic:
        nm = b.get("bond_short_name") or ""
        if nm in known:
            checks.append({"name": nm, "ts_code": b["ts_code"],
                           "list_date": b.get("list_date"), "delist_date": b.get("delist_date"),
                           "maturity_date": b.get("maturity_date"),
                           "expect": known[nm]})
    rep["d5_known_checks"] = checks
    for c in checks:
        print("D5 known: %s delist=%s maturity=%s expect~%s" % (
            c["name"], c["delist_date"], c["maturity_date"], c["expect"]))

    # D3 汇总已有；D7 汇总
    rep["d3"] = d2["d3"]
    rep["d4_coverage"] = d2["d4_coverage"]
    rep["d7_alive_not_traded"] = d2["d7_alive_not_traded"]
    rep["d7_counts"] = {"total": 1164, "delisted": 817, "alive_cb": 345,
                        "traded_20260911": 315}
    # 定转/私募识别（名称含"定"或 K1/S1 等）
    priv = [c for c in d2["d7_alive_not_traded"] if ("定" in str(c)) or c.endswith(("K1", "S1"))]
    real_dead = [c for c in d2["d7_alive_not_traded"] if c not in priv]
    rep["d7_real_dead_alive"] = real_dead
    print("D7 alive_not_traded: private=%d real=%s" % (len(priv), real_dead))

    with open(os.path.join(OUT, "de_r3_cb_analysis.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    print("[SAVED] de_r3_cb_analysis.json")


if __name__ == "__main__":
    main()
