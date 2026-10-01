# -*- coding: utf-8 -*-
"""开盘实时复核数据采集 - 2026-09-29（T-1=20260928 候选）

按源职责分组 + 熔断路由（scripts/data_source_router）：
  ① 基础行情  腾讯 qt.gtimg.cn  →  东财 push2 stock/get（交叉校验/补量比PE换手）
  ② F2 资金   本地 Tushare moneyflow parquet（权威 T-1，review_full 自动读）
              → 东财 ulist f62（当日，作为 JSON 内当日口径，主源缺失时兜底）
  ③ F3 催化   东财 search-api-web 个股新闻
  ④ F5 板块   东财 slist spt=3（所属行业板块当日涨跌幅）
  TDX 系（mcp_tdx）本轮不可达，已 record fail，按规约降级不阻塞。
"""
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import scripts.data_source_router as router
import pandas as pd

TODAY = "20260929"
DATA_DATE = "20260928"
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

CAND_FILE = os.path.join(HERE, "data", "selections", "%s_model_top10.csv" % DATA_DATE)
STOCKS = list(pd.read_csv(CAND_FILE)["ts_code"].tolist())
print("候选(%d, 数据日 %s): %s" % (len(STOCKS), DATA_DATE, STOCKS))

H_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
H_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
errors = []


def _get(url, headers, timeout=8):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout
                                  ).read().decode("utf-8", errors="replace")


def tcode(ts):
    c, m = ts.split(".")
    return ("sh" if m == "SH" else "sz") + c


def secid(ts):
    c, m = ts.split(".")
    return ("1." if m == "SH" else "0.") + c


# ---------- ① 基础行情：腾讯 ----------
def fetch_tencent():
    out = {}
    if router.check("tencent") == "OPEN":
        print("[SKIP] tencent OPEN")
        return out
    url = "https://qt.gtimg.cn/q=" + ",".join(tcode(c) for c in STOCKS)
    t0 = time.time()
    try:
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=H_TX), timeout=8
                                     ).read().decode("gbk", errors="replace")
        router.record("tencent", True, int((time.time() - t0) * 1000))
        for line in raw.strip().split("\n"):
            if '="' not in line:
                continue
            f = line.split('="', 1)[1].rstrip('";').split("~")
            if len(f) < 40:
                continue
            code, mkt = f[2], ("SH" if f[2].startswith("6") else "SZ")
            ts = "%s.%s" % (code, mkt)

            def fl(i):
                try:
                    return float(f[i])
                except Exception:
                    return None
            out[ts] = {"name": f[1], "last": fl(3), "open": fl(5), "high": fl(33),
                       "low": fl(34), "quote_pct": fl(32), "volume": fl(6),
                       "amount_wan": fl(37), "turnover": fl(38), "pe_ttm": fl(39)}
        print("[1] 腾讯基础行情 %d/%d" % (len(out), len(STOCKS)))
    except Exception as e:
        router.record("tencent", False, int((time.time() - t0) * 1000))
        errors.append("tencent: %s" % str(e)[:100])
        print("[ERR] tencent %s" % str(e)[:100])
    return out


# ---------- ①b 东财扩展：量比/PE/换手 ----------
def fetch_em_ext():
    out = {}
    if router.check("eastmoney_curl") == "OPEN":
        print("[SKIP] eastmoney_curl OPEN")
        return out
    fields = "f43,f46,f47,f48,f50,f57,f58,f60,f85,f116,f117,f162,f169,f170"
    for ts in STOCKS:
        t0 = time.time()
        try:
            d = (json.loads(_get("https://push2.eastmoney.com/api/qt/stock/get?secid=%s&fields=%s"
                                % (secid(ts), fields), H_EM)).get("data") or {})
            router.record("eastmoney_curl", True, int((time.time() - t0) * 1000))

            def n(k, div=1.0):
                v = d.get(k)
                if v in (None, "-", ""):
                    return None
                return round(v / div, 4) if div != 1.0 else v
            turn = None
            if d.get("f47") and d.get("f85"):
                turn = round(d["f47"] * 100.0 / d["f85"] * 100.0, 2)
            out[ts] = {"last": n("f43", 100), "open": n("f46", 100), "quote_pct": n("f170", 100),
                       "liangbi": n("f50", 100), "pe_ttm": n("f162", 100), "turnover": turn,
                       "amount_yuan": n("f48"), "float_mv": n("f117"), "source": "eastmoney_curl"}
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("em_ext %s: %s" % (ts, str(e)[:80]))
    print("[1b] 东财扩展 %d/%d" % (len(out), len(STOCKS)))
    return out


# ---------- ② F2 当日主力资金 ----------
def fetch_f2():
    if router.check("eastmoney_curl") == "OPEN":
        print("[SKIP] eastmoney F2 OPEN")
        return {}
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=%s"
           "&fields=f12,f13,f14,f62,f184,f124" % ",".join(secid(c) for c in STOCKS))
    t0 = time.time()
    out = {}
    try:
        diff = (json.loads(_get(url, H_EM)).get("data") or {}).get("diff") or []
        router.record("eastmoney_curl", True, int((time.time() - t0) * 1000))
        for d in diff:
            ts = "%s.%s" % (d.get("f12"), "SH" if str(d.get("f13")) == "1" else "SZ")
            ts = ts if ts.endswith(("SH", "SZ")) else None
            out[ts] = {"main_net_inflow": d.get("f62"), "main_net_ratio": d.get("f184"),
                       "main_net_inflow_date": TODAY, "source": "eastmoney_curl"}
        print("[2] 东财当日主力资金 %d/%d" % (len(out), len(STOCKS)))
    except Exception as e:
        router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
        errors.append("f2: %s" % str(e)[:100])
    return out


# ---------- ③ F3 催化新闻 ----------
def fetch_f3():
    cats = {}
    if router.check("eastmoney_curl") == "OPEN":
        print("[SKIP] 东财 F3 OPEN")
        return cats
    for ts in STOCKS:
        code = ts.split(".")[0]
        param = {"uid": "", "keyword": code, "type": ["cmsArticleWebOld"], "client": "web",
                 "clientType": "web", "clientVersion": "curr",
                 "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time",
                                                "pageIndex": 1, "pageSize": 6}}}
        url = ("https://search-api-web.eastmoney.com/search/jsonp?cb=cb&param="
               + urllib.parse.quote(json.dumps(param, ensure_ascii=False)))
        t0 = time.time()
        try:
            raw = _get(url, H_EM)
            router.record("eastmoney_curl", True, int((time.time() - t0) * 1000))
            body = raw[raw.find("(") + 1:raw.rfind(")")]
            items = ((json.loads(body).get("result") or {}).get("cmsArticleWebOld") or [])
            news = [{"title": (it.get("title") or "").replace("<em>", "").replace("</em>", ""),
                     "date": (it.get("date") or "")[:10]} for it in items[:6]]
            cats[ts] = news
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("f3 %s: %s" % (ts, str(e)[:60]))
            cats[ts] = []
    print("[3] F3 新闻 %d 只命中 %d 只" % (len(cats), sum(1 for v in cats.values() if v)))
    return cats


# ---------- ④ F5 所属行业板块涨幅 ----------
def fetch_f5():
    out = {}
    if router.check("eastmoney_curl") == "OPEN":
        print("[SKIP] 东财 F5 OPEN")
        return out
    for ts in STOCKS:
        url = ("https://push2.eastmoney.com/api/qt/slist/get?spt=3&fltt=2&invt=2"
               "&fields=f12,f13,f14,f3&secid=%s&pn=1&pz=20" % secid(ts))
        t0 = time.time()
        try:
            d = json.loads(_get(url, H_EM)).get("data") or {}
            router.record("eastmoney_curl", True, int((time.time() - t0) * 1000))
            diff = d.get("diff") or {}
            first = diff.get("0") if isinstance(diff, dict) else (diff[0] if diff else None)
            if first:
                out[ts] = {"industry_pct": first.get("f3"), "industry": first.get("f14"),
                           "industry_code": first.get("f12"), "source": "eastmoney_curl"}
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("f5 %s: %s" % (ts, str(e)[:60]))
    print("[4] F5 板块 %d/%d" % (len(out), len(STOCKS)))
    return out


def main():
    # 顺序按关键度：腾讯行情 → F2 资金 → F5 板块 → F3 新闻 → 东财补充(量比/PE)
    # 东财补充放最后：它请求数最多，一旦熔断不能连带影响 F2/F5
    tq = fetch_tencent()
    f2 = fetch_f2()
    f5 = fetch_f5()
    f3 = fetch_f3()
    em = fetch_em_ext()

    stocks = []
    for ts in STOCKS:
        t, e, f, n, b = tq.get(ts, {}), em.get(ts, {}), f2.get(ts, {}), f3.get(ts, []), f5.get(ts, {})
        price = t.get("last") if t.get("last") is not None else e.get("last")
        pct = t.get("quote_pct") if t.get("quote_pct") is not None else e.get("quote_pct")
        stocks.append({
            "ts_code": ts,
            "name": t.get("name") or "",
            "last": price,
            "open": t.get("open") if t.get("open") is not None else e.get("open"),
            "quote_pct": pct,
            "volume": t.get("volume"),
            "amount_wan": t.get("amount_wan"),
            "liangbi": e.get("liangbi"),
            "turnover": t.get("turnover") if t.get("turnover") is not None else e.get("turnover"),
            "pe_ttm": t.get("pe_ttm") if t.get("pe_ttm") is not None else e.get("pe_ttm"),
            "main_net_inflow": f.get("main_net_inflow"),
            "main_net_ratio": f.get("main_net_ratio"),
            "main_net_inflow_date": f.get("main_net_inflow_date"),
            "industry": b.get("industry"),
            "industry_pct": b.get("industry_pct"),
            "catalyst_note": "; ".join("%s(%s)" % (x["title"], x["date"]) for x in n[:3]),
            "news_count": len(n),
            "source": "tencent+eastmoney_curl",
        })

    out = {"date": TODAY, "data_date": DATA_DATE,
           "collected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "f2_primary": "tushare_local_T-1(D:/astock/moneyflow/moneyflow.parquet)",
           "stocks": stocks, "errors": errors}
    p = os.path.join(HERE, "data", "tdx_review.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[OUT] %s" % p)

    with open(os.path.join(CACHE, "review_%s.json" % TODAY), "w", encoding="utf-8") as f:
        json.dump({"date": TODAY, "collected_at": out["collected_at"],
                   "catalysts": {k: {"catalyst_score": None,
                                     "catalyst_note": "; ".join("%s(%s)" % (x["title"], x["date"]) for x in v[:3]),
                                     "news_count": len(v)} for k, v in f3.items()}},
                  f, ensure_ascii=False, indent=2)

    print("\n%-11s %-8s %8s %7s %7s %7s %8s %13s %7s  %s"
          % ("code", "name", "last", "pct%", "量比", "换手", "PE", "F2主力(万)", "板块%", "新闻"))
    for s in stocks:
        f2w = ("%.0f" % (s["main_net_inflow"] / 1e4)) if s.get("main_net_inflow") is not None else "-"
        print("%-11s %-8s %8s %7s %7s %7s %8s %13s %7s  %d"
              % (s["ts_code"], s["name"][:6], s["last"], s["quote_pct"], s["liangbi"],
                 s["turnover"], s["pe_ttm"], f2w, s["industry_pct"], s["news_count"]))
    print("\nerrors: %d" % len(errors))
    for e in errors[:5]:
        print("  ", e)


if __name__ == "__main__":
    main()
