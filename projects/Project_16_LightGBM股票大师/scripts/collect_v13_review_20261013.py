# -*- coding: utf-8 -*-
"""V1.3（账号 67014907）开盘实时复核取数 - 20261013

只采集 data/selections/20261012_model_top10.csv 的候选（已校验 trade_date=20261012 = 面板最新日）。
严格不混入 G2（账号 70180771）候选/持仓，避免跨账户数据污染下单决策。

职责分组（v8）：
  ① 基础行情  腾讯 qt.gtimg.cn（价/量/换手/PE）
  ② 扩展指标  东财 push2 stock/get（量比/PE/换手交叉）
  ③ F3 催化   东财 search-api-web 个股新闻（仅取回事实，评分由人工判定后回填）
  ④ F5 板块   东财 slist spt=3 所属行业板块涨幅

F2 主力资金：不写 main_net_inflow —— review_full.py 自动读本地 Tushare moneyflow
（T-1 权威口径，新鲜 lag<=2 时作主源），避免实时近似口径污染。

每个外部源调用前 router.check，调用后 router.record（失败必记，连续 3 次熔断 300s）。
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

TODAY = "20261013"
EXPECT_DATE = "20261012"
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

H_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
H_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
errors = []


def _read_codes(path):
    if not os.path.exists(path):
        return [], ""
    try:
        df = pd.read_csv(path, dtype={"ts_code": str})
        if "ts_code" not in df.columns or len(df) == 0:
            return [], ""
        codes = [c.strip() for c in df["ts_code"].astype(str).tolist()]
        d = str(df["trade_date"].iloc[0]).strip() if "trade_date" in df.columns else ""
        return codes, d
    except Exception as e:
        errors.append("read %s: %s" % (os.path.basename(path), str(e)[:80]))
        return [], ""


M_FILE = os.path.join(HERE, "data", "selections", "%s_model_top10.csv" % EXPECT_DATE)
M_CANDS, M_DATE = _read_codes(M_FILE)
print("V1.3 候选(%d, trade_date=%s): %s" % (len(M_CANDS), M_DATE, M_CANDS))
if not M_CANDS:
    print("[FATAL] 候选为空，终止")
    sys.exit(2)
if M_DATE != EXPECT_DATE:
    print("[FATAL] 候选 trade_date=%s 与面板最新日 %s 不一致，终止（防陈旧候选下单）" % (M_DATE, EXPECT_DATE))
    sys.exit(2)

STOCKS = M_CANDS


def _get(url, headers, timeout=8):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout
                                  ).read().decode("utf-8", errors="replace")


def tcode(ts):
    c, m = ts.split(".")
    return ("sh" if m == "SH" else "sz" if m == "SZ" else "bj") + c


def secid(ts):
    c, m = ts.split(".")
    return ("1." if m == "SH" else "0.") + c


# ---------- ① 基础行情：腾讯 ----------
def fetch_tencent():
    out = {}
    if router.check("tencent") == "OPEN":
        print("[SKIP] tencent OPEN")
        return out
    t0 = time.time()
    try:
        url = "https://qt.gtimg.cn/q=" + ",".join(tcode(c) for c in STOCKS)
        raw = urllib.request.urlopen(urllib.request.Request(url, headers=H_TX), timeout=10
                                     ).read().decode("gbk", errors="replace")
        for line in raw.strip().split("\n"):
            if '="' not in line:
                continue
            f = line.split('="', 1)[1].rstrip('";').split("~")
            if len(f) < 40:
                continue
            code = f[2]
            mkt = "SH" if code.startswith("6") else ("BJ" if code.startswith("9") else "SZ")
            ts = "%s.%s" % (code, mkt)

            def fl(i):
                try:
                    return float(f[i])
                except Exception:
                    return None
            out[ts] = {"name": f[1], "last": fl(3), "open": fl(5), "high": fl(33),
                       "low": fl(34), "quote_pct": fl(32), "volume": fl(6),
                       "amount_wan": fl(37), "turnover": fl(38), "pe_ttm": fl(39),
                       "quote_time": f[30] if len(f) > 30 else ""}
        router.record("tencent", True, int((time.time() - t0) * 1000))
        print("[1] 腾讯基础行情 %d/%d" % (len(out), len(STOCKS)))
    except Exception as e:
        router.record("tencent", False, int((time.time() - t0) * 1000))
        errors.append("tencent: %s" % str(e)[:100])
        print("[ERR] tencent %s" % str(e)[:100])
    return out


# ---------- ② 东财扩展：量比/PE/换手 ----------
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
                       "amount_yuan": n("f48"), "float_mv": n("f117")}
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("em_ext %s: %s" % (ts, str(e)[:80]))
    print("[2] 东财扩展 %d/%d" % (len(out), len(STOCKS)))
    return out


# ---------- ③ F3 催化新闻（只取事实，评分人工判定后回填） ----------
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
            body = raw[raw.find("(") + 1:raw.rfind(")")]
            items = ((json.loads(body).get("result") or {}).get("cmsArticleWebOld") or [])
            cats[ts] = [{"title": (it.get("title") or "").replace("<em>", "").replace("</em>", ""),
                         "date": (it.get("date") or "")[:10]} for it in items[:6]]
            router.record("eastmoney_curl", True, int((time.time() - t0) * 1000))
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("f3 %s: %s" % (ts, str(e)[:60]))
            cats[ts] = []
    print("[3] F3 新闻 覆盖 %d/%d" % (sum(1 for v in cats.values() if v), len(STOCKS)))
    return cats


# ---------- ④ F5 板块涨幅 ----------
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
                           "industry_code": first.get("f12")}
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("f5 %s: %s" % (ts, str(e)[:60]))
    print("[4] F5 板块 %d/%d" % (len(out), len(STOCKS)))
    return out


def main():
    tq = fetch_tencent()
    em = fetch_em_ext()
    f5 = fetch_f5()
    f3 = fetch_f3()

    stocks = []
    for ts in STOCKS:
        t, e, b, n = tq.get(ts, {}), em.get(ts, {}), f5.get(ts, {}), f3.get(ts, [])
        stocks.append({
            "ts_code": ts,
            "name": t.get("name") or "",
            "last": t.get("last") if t.get("last") is not None else e.get("last"),
            "open": t.get("open") if t.get("open") is not None else e.get("open"),
            "high": t.get("high"), "low": t.get("low"),
            "quote_pct": t.get("quote_pct") if t.get("quote_pct") is not None else e.get("quote_pct"),
            "volume": t.get("volume"), "amount_wan": t.get("amount_wan"),
            "liangbi": e.get("liangbi"),
            "turnover": t.get("turnover") if t.get("turnover") is not None else e.get("turnover"),
            "pe_ttm": t.get("pe_ttm") if t.get("pe_ttm") is not None else e.get("pe_ttm"),
            "quote_time": t.get("quote_time"),
            "industry": b.get("industry"), "industry_pct": b.get("industry_pct"),
            "news": n[:4],
            "news_count": len(n),
            "catalyst_note": "; ".join("%s(%s)" % (x["title"], x["date"]) for x in n[:3]),
            "source": "tencent+eastmoney_curl",
        })

    news_path = os.path.join(CACHE, "f3_news_%s.json" % TODAY)
    with open(news_path, "w", encoding="utf-8") as f:
        json.dump({"date": TODAY, "news": f3}, f, ensure_ascii=False, indent=2)

    out = {"date": TODAY,
           "data_date": M_DATE,
           "collected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "quote_kind": "盘中实时行情",
           "account": "67014907",
           "f2_primary": "tushare_local_T-1 (D:/astock/moneyflow/moneyflow.parquet) — 本 JSON 不填 main_net_inflow，由 review_full.py 自动取权威口径",
           "candidates": M_CANDS,
           "stocks": stocks, "errors": errors}
    p = os.path.join(HERE, "data", "tdx_review.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    print("[OUT] %s" % p)
    print("[OUT] %s" % news_path)

    print("\n%-11s %-8s %8s %7s %7s %7s %8s %8s  %s"
          % ("code", "name", "last", "pct%", "量比", "换手", "PE", "板块%", "新闻"))
    for s in stocks:
        print("%-11s %-8s %8s %7s %7s %7s %8s %8s  %d"
              % (s["ts_code"], (s["name"] or "")[:6], s["last"], s["quote_pct"], s["liangbi"],
                 s["turnover"], s["pe_ttm"], s["industry_pct"], s["news_count"]))
    print("\nquote_time(腾讯): %s" % ",".join("%s=%s" % (s["ts_code"], s["quote_time"])
                                            for s in stocks[:4]))
    print("errors: %d" % len(errors))
    for e in errors[:10]:
        print("  ", e)


if __name__ == "__main__":
    main()
