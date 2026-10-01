# -*- coding: utf-8 -*-
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = "20260930"

raw = json.load(open(
    os.path.join(HERE, "data", "premarket_%s_raw.json" % TODAY),
    encoding="utf-8"))
cc = json.load(open(
    os.path.join(HERE, "data", "cache", "crosscheck_%s.json" % TODAY),
    encoding="utf-8"))

by = dict((s["ts_code"], s) for s in raw["stocks"])
g2c = raw.get("g2_candidates") or []
g2top3 = raw.get("g2_top3") or []
holds = raw.get("g2_holdings") or []

scores = {}
subs = {}
p = os.path.join(HERE, "data", "selections", "g2",
                 "%s_g2_top10.csv" % TODAY)
if os.path.exists(p):
    import csv
    with open(p, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ts = (row.get("ts_code") or "").strip()
            try:
                scores[ts] = float(row.get("total_new"))
            except Exception:
                pass
            try:
                subs[ts] = dict(
                    (k, float(row.get("SC_%s" % k) or 0))
                    for k in ("F1", "F2", "F3", "F4", "F5", "F6")
                )
            except Exception:
                subs[ts] = {}


def wan(v):
    if v is None:
        return None
    return round(v / 10000.0, 2)


print("=" * 78)
print("[G2 持仓预警]  独立账本 hold_dates + T-1 权威F2 + 当日行情")
print("=" * 78)
hold_rows = []
for ts in holds:
    s = by.get(ts) or {}
    t2 = s.get("f2_tushare") or {}
    em = s.get("f2_eastmoney") or {}
    hold_rows.append({
        "ts_code": ts,
        "name": s.get("name"),
        "last": s.get("last"),
        "quote_pct": s.get("quote_pct"),
        "cost_hint": None,
        "f2_t1_wan": wan(t2.get("main_net_inflow")),
        "f2_t1_date": t2.get("main_net_inflow_date"),
        "em_today_wan": wan(em.get("main_net_inflow_em")),
        "em_ratio": em.get("main_net_ratio_em"),
        "industry": s.get("industry"),
        "industry_pct": s.get("industry_pct"),
    })
    print("%-11s %-6s last=%-8s pct=%-7s F2_T1=%-9s EM今日=%-9s 行业=%s(%s)" % (
        ts, s.get("name"), s.get("last"), s.get("quote_pct"),
        wan(t2.get("main_net_inflow")),
        wan(em.get("main_net_inflow_em")),
        s.get("industry"), s.get("industry_pct")))

print()
print("=" * 78)
print("[G2 候选强弱]  score + 当日涨跌 + F2(T-1) + 资金流一致度")
print("=" * 78)
cand_rows = []
for ts in g2c:
    s = by.get(ts) or {}
    t2 = s.get("f2_tushare") or {}
    em = s.get("f2_eastmoney") or {}
    a = wan(t2.get("main_net_inflow"))
    b = wan(em.get("main_net_inflow_em"))
    direc = None
    if a is not None and b is not None:
        direc = "同向" if (a > 0) == (b > 0) else "背离"
    cand_rows.append({
        "ts_code": ts,
        "name": s.get("name"),
        "score": scores.get(ts),
        "score_subs": subs.get(ts) or {},
        "last": s.get("last"),
        "quote_pct": s.get("quote_pct"),
        "liangbi": s.get("liangbi"),
        "turnover": s.get("turnover"),
        "pe_ttm": s.get("pe_ttm"),
        "f2_t1_wan": a,
        "em_today_wan": b,
        "em_ratio": em.get("main_net_ratio_em"),
        "f2_direction": direc,
        "industry": s.get("industry"),
        "industry_pct": s.get("industry_pct"),
        "is_top3": ts in g2top3,
        "holding": ts in holds,
    })
    print("%-11s %-6s score=%-5s pct=%-7s 量比=%-6s F2_T1=%-9s EM今日=%-9s %s" % (
        ts, s.get("name"), scores.get(ts), s.get("quote_pct"),
        s.get("liangbi"), a, b, direc or "-"))

print()
print("=" * 78)
print("[D_model 09:15候选(实为 %s 旧候选)] 交叉验证" % raw.get("m915_data_date"))
print("=" * 78)
ccs = cc.get("stocks") or {}
for ts in (raw.get("m915_candidates") or []):
    s = by.get(ts) or {}
    c = ccs.get(ts) or {}
    q = c.get("quote") or {}
    print("%-11s %-6s last=%-8s 对拍=%-5s diff=%-7s 板块双源=%-5s" % (
        ts, s.get("name"), s.get("last"),
        q.get("ok"), q.get("diff_pct"),
        (c.get("sector") or {}).get("ok")))

json.dump({
    "holdings": hold_rows,
    "candidates": cand_rows,
}, open(os.path.join(HERE, "data", "cache",
                      "analysis_%s.json" % TODAY), "w",
          encoding="utf-8"), ensure_ascii=False, indent=2)
print()
print("[OK] analysis -> data/cache/analysis_%s.json" % TODAY)
print("errors=%d" % len(raw.get("errors") or []))
