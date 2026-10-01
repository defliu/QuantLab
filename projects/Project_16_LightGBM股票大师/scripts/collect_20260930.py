# -*- coding: utf-8 -*-
"""盘前集合竞价预判数据采集 - 2026-09-30

标的动态读取（不硬编码）：
  - G2 候选   data/selections/g2/20260930_g2_top10.csv
  - G2 持仓   data/rebalance_g2/g2_hold_dates.json（独立虚拟子账户账本）
  - 09:15 候选 data/selections/D_model_top10.csv（步骤3.5/3.6 用）

职责分组（v8）：
  ① 基础行情  腾讯 qt.gtimg.cn → 东财 push2 stock/get（补量比/PE/换手）
  ② F2 资金   本地 Tushare moneyflow parquet 权威 T-1 → 东财 ulist f62 当日兜底
  ③ F3 催化   东财 search-api-web 个股新闻
  ④ F5 板块   东财 slist spt=3 所属行业板块涨幅

每个源调用前 router.check，调用后 router.record（失败必记，连续3次熔断300s）。
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

TODAY = "20260930"
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

H_TX = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
H_EM = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
errors = []


def _read_codes(path):
    if not os.path.exists(path):
        return []
    try:
        df = pd.read_csv(path)
        if "ts_code" not in df.columns or len(df) == 0:
            return []
        return list(df["ts_code"].astype(str).tolist())
    except Exception as e:
        errors.append("read %s: %s" % (os.path.basename(path), str(e)[:80]))
        return []


G2_FILE = os.path.join(HERE, "data", "selections", "g2", "%s_g2_top10.csv" % TODAY)
G2_CANDS = _read_codes(G2_FILE)
G2_TOP_FILE = os.path.join(HERE, "data", "selections", "g2", "%s_g2_top3.csv" % TODAY)
G2_TOP3 = _read_codes(G2_TOP_FILE)
M_FILE = os.path.join(HERE, "data", "selections", "D_model_top10.csv")
M_CANDS = _read_codes(M_FILE)
M_DATE = ""
if M_CANDS:
    try:
        M_DATE = str(pd.read_csv(M_FILE)["trade_date"].iloc[0])
    except Exception:
        M_DATE = ""

HOLD_FILE = os.path.join(HERE, "data", "rebalance_g2", "g2_hold_dates.json")
G2_HOLDS = []
if os.path.exists(HOLD_FILE):
    try:
        with open(HOLD_FILE, encoding="utf-8") as f:
            G2_HOLDS = list((json.load(f).get("hold_dates") or {}).keys())
    except Exception as e:
        errors.append("hold_dates: %s" % str(e)[:80])

STOCKS = []
for c in G2_CANDS + G2_HOLDS + M_CANDS:
    if c not in STOCKS:
        STOCKS.append(c)
print("G2候选(%d): %s" % (len(G2_CANDS), G2_CANDS))
print("G2Top3: %s" % G2_TOP3)
print("G2独立持仓(%d): %s" % (len(G2_HOLDS), G2_HOLDS))
print("09:15候选(%d, 数据日 %s): %s" % (len(M_CANDS), M_DATE, M_CANDS))
print("合并采集标的(%d): %s" % (len(STOCKS), STOCKS))


def _get(url, headers, timeout=8):
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout
                                  ).read().decode("utf-8", errors="replace")


def tcode(ts):
    c, m = ts.split(".")
    if m == "SH":
        return "sh" + c
    if m == "SZ":
        return "sz" + c
    return "bj" + c


def secid(ts):
    c, m = ts.split(".")
    if m == "SH":
        return "1." + c
    if m == "SZ":
        return "0." + c
    return "0." + c


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


# ---------- ①b 东财扩展：量比/PE/换手/市值 ----------
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


# ---------- ② F2 资金：本地 Tushare 权威 T-1 ----------
def fetch_f2_tushare():
    out, meta = {}, {}
    p = r"D:\astock\moneyflow\moneyflow.parquet"
    if not os.path.exists(p):
        errors.append("tushare moneyflow parquet 缺失: %s" % p)
        return out, meta
    if router.check("eastmoney_curl") == "OPEN":  # 本地文件不熔断，仅登记路由存在
        pass
    t0 = time.time()
    try:
        df = pd.read_parquet(p)
        if isinstance(df.index, pd.MultiIndex):
            df = df.reset_index()
        dcol = "trade_date" if "trade_date" in df.columns else None
        ccol = "ts_code" if "ts_code" in df.columns else None
        if dcol is None or ccol is None:
            raise ValueError("moneyflow 缺 ts_code/trade_date 列: %s" % list(df.columns)[:5])
        df["_d"] = df[dcol].astype(str).str[:10].str.replace("-", "", regex=False)
        latest_s = df["_d"].max()
        sub = df[df["_d"] == latest_s]
        for _, r in sub.iterrows():
            ts = str(r[ccol])
            lg = (r.get("buy_lg_amount") or 0) - (r.get("sell_lg_amount") or 0)
            elg = (r.get("buy_elg_amount") or 0) - (r.get("sell_elg_amount") or 0)
            nmf = r.get("net_mf_amount")
            out[ts] = {"main_net_inflow": round(lg + elg, 2),
                       "main_net_inflow_date": latest_s,
                       "net_mf_amount": float(nmf) if nmf is not None else None,
                       "source": "tushare_local_parquet"}
        meta = {"date": latest_s, "rows": int(len(sub)), "elapsed_ms": int((time.time() - t0) * 1000)}
        print("[2] Tushare moneyflow 权威口径 %s 行%d -> 命中 %d/%d"
              % (latest_s, len(sub), len(out), len(STOCKS)))
    except Exception as e:
        errors.append("f2_tushare: %s" % str(e)[:100])
        print("[ERR] f2_tushare %s" % str(e)[:100])
    return out, meta


def fetch_f2_em():
    out = {}
    if router.check("eastmoney_curl") == "OPEN":
        print("[SKIP] eastmoney F2 OPEN")
        return out
    url = ("https://push2.eastmoney.com/api/qt/ulist.np/get?fltt=2&secids=%s"
           "&fields=f12,f13,f14,f62,f184" % ",".join(secid(c) for c in STOCKS))
    t0 = time.time()
    try:
        diff = (json.loads(_get(url, H_EM)).get("data") or {}).get("diff") or []
        router.record("eastmoney_curl", True, int((time.time() - t0) * 1000))
        for d in diff:
            c = str(d.get("f12"))
            mkt = "SH" if str(d.get("f13")) == "1" else ("BJ" if c.startswith("9") else "SZ")
            out["%s.%s" % (c, mkt)] = {"main_net_inflow_em": d.get("f62"),
                                       "main_net_ratio_em": d.get("f184"),
                                       "source": "eastmoney_curl"}
        print("[2b] 东财当日主力资金 %d/%d" % (len(out), len(STOCKS)))
    except Exception as e:
        router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
        errors.append("f2_em: %s" % str(e)[:100])
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
                           "industry_code": first.get("f12"), "source": "eastmoney_curl"}
        except Exception as e:
            router.record("eastmoney_curl", False, int((time.time() - t0) * 1000))
            errors.append("f5 %s: %s" % (ts, str(e)[:60]))
    print("[4] F5 板块 %d/%d" % (len(out), len(STOCKS)))
    return out


def main():
    tq = fetch_tencent()
    f2, f2meta = fetch_f2_tushare()
    f2em = fetch_f2_em()
    f5 = fetch_f5()
    f3 = fetch_f3()
    em = fetch_em_ext()

    stocks = []
    for ts in STOCKS:
        t, e = tq.get(ts, {}), em.get(ts, {})
        f, fe, b, n = f2.get(ts, {}), f2em.get(ts, {}), f5.get(ts, {}), f3.get(ts, [])
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
            "f2_tushare": f, "f2_eastmoney": fe,
            "industry": b.get("industry"), "industry_pct": b.get("industry_pct"),
            "news": n[:4],
            "catalyst_note": "; ".join("%s(%s)" % (x["title"], x["date"]) for x in n[:3]),
            "news_count": len(n),
            "role": ("g2_cand" if ts in G2_CANDS else "") + ("|g2_hold" if ts in G2_HOLDS else "")
                    + ("|m915" if ts in M_CANDS else ""),
            "source": "tencent+eastmoney_curl+tushare_local",
        })

    out = {"date": TODAY, "collected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "quote_kind": "盘中实时行情(采集时刻晚于09:25，非严格集合竞价快照)",
           "g2_candidates": G2_CANDS, "g2_top3": G2_TOP3, "g2_holdings": G2_HOLDS,
           "m915_candidates": M_CANDS, "m915_data_date": M_DATE,
           "f2_primary": "tushare_local_T-1(%s)" % f2meta.get("date"),
           "f2_meta": f2meta,
           "stocks": stocks, "errors": errors}
    p = os.path.join(HERE, "data", "premarket_20260930_raw.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("[OUT] %s" % p)

    print("\n%-11s %-8s %8s %7s %7s %7s %8s %14s %8s %7s %4s  %s"
          % ("code", "name", "last", "pct%", "量比", "换手", "PE", "F2主力T-1(万)", "EM当日(万)",
             "板块%", "新闻", "角色"))
    for s in stocks:
        f2w = ("%.0f" % (s["f2_tushare"]["main_net_inflow"] / 1e4)
               if s["f2_tushare"].get("main_net_inflow") is not None else "-")
        emw = ("%.0f" % (s["f2_eastmoney"]["main_net_inflow_em"] / 1e4)
               if s["f2_eastmoney"].get("main_net_inflow_em") is not None else "-")
        print("%-11s %-8s %8s %7s %7s %7s %8s %14s %8s %7s %4d  %s"
              % (s["ts_code"], (s["name"] or "")[:6], s["last"], s["quote_pct"], s["liangbi"],
                 s["turnover"], s["pe_ttm"], f2w, emw, s["industry_pct"], s["news_count"], s["role"]))
    print("\nquote_time(腾讯): %s" % ",".join("%s=%s" % (s["ts_code"], s["quote_time"])
                                            for s in stocks[:6]))
    print("errors: %d" % len(errors))
    for e in errors[:8]:
        print("  ", e)


if __name__ == "__main__":
    main()
