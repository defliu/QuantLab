# -*- coding: utf-8 -*-
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = "20260930"

RAW = os.path.join(HERE, "data", "premarket_%s_raw.json" % TODAY)
OUT = os.path.join(HERE, "data", "cache", "review_%s.json" % TODAY)

with open(RAW, "r", encoding="utf-8") as f:
    raw = json.load(f)

stocks = raw.get("stocks") or []
cands = raw.get("m915_candidates") or []
by_code = dict((s.get("ts_code"), s) for s in stocks)

catalysts = {}
for ts in cands:
    s = by_code.get(ts) or {}
    note = s.get("catalyst_note")
    if not note:
        news = s.get("news") or []
        if news:
            note = "; ".join(
                "%s %s" % (n.get("date") or "", n.get("title") or "")
                for n in news[:2]
            )
    catalysts[ts] = {
        "catalyst_score": None,
        "catalyst_note": note or None,
        "name": s.get("name"),
        "news_count": s.get("news_count"),
        "catalyst_source": "eastmoney_search_news(%s)" % TODAY,
        "note": (
            "东财个股新闻搜索口径，非事件性评分；"
            "9:45 实时复核缺失 catalyst 时可兜底使用"
        ),
    }

out = {
    "date": TODAY,
    "generated_at": raw.get("collected_at"),
    "source_candidates": "data/selections/D_model_top10.csv",
    "source_candidate_date": raw.get("m915_data_date"),
    "feature_panel_latest": "20260929",
    "catalysts": catalysts,
}

tmp = OUT + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
os.replace(tmp, OUT)

print("[OK] review cache -> %s" % OUT)
print("candidates=%d  with_note=%d" % (
    len(catalysts),
    len([v for v in catalysts.values() if v["catalyst_note"]])
))
for ts, v in catalysts.items():
    print("  %s %s | news=%s | %s" % (
        ts, v.get("name"), v.get("news_count"),
        (v.get("catalyst_note") or "")[:70]
    ))
