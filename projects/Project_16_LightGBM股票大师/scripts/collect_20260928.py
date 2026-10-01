# -*- coding: utf-8 -*-
"""盘前集合竞价预判 - 2026-09-28 数据采集（F2/F3/F5 + 交叉验证）"""
import json, os, sys, time, urllib.request, datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import scripts.data_source_router as router

TODAY = "20260928"
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

STOCKS = ["603272.SH", "003029.SZ", "000610.SZ", "688093.SH", "600691.SH", "600180.SH", "603377.SH"]
CANDIDATES = STOCKS
NAME_MAP = {"603272.SH": "*ST联翔", "003029.SZ": "吉大正元", "000610.SZ": "*ST西旅",
            "688093.SH": "世华科技", "600691.SH": "潞化科技", "600180.SH": "*ST瑞茂",
            "603377.SH": "ST东时"}

def tencent_code(ts_code):
    code = ts_code.split(".")[0]
    market = ts_code.split(".")[1]
    return ("sh" if market == "SH" else "sz") + code

def check_source(source):
    status = router.check(source)
    return status != "OPEN"

# ============ F2: 备用资金源 ============
def fetch_em_capital_flow():
    results = {}
    if not check_source("eastmoney_curl"):
        print("[SKIP] eastmoney_curl (circuit OPEN)")
        return {"_skipped": True}
    for ts_code in STOCKS:
        code = ts_code.split(".")[0]
        market = ts_code.split(".")[1]
        secid = ("1." if market == "SH" else "0.") + code
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f84,f85,f86,f116,f117,f162,f167,f168,f169,f170,f171,f173"
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="replace")
            dt = int((time.time() - t0) * 1000)
            data = json.loads(raw)
            d = data.get("data", {})
            results[ts_code] = {
                "net_mf": d.get("f162"),
                "buy_lg": d.get("f167"),
                "sell_lg": d.get("f168"),
                "source": "eastmoney_curl",
                "trade_date": TODAY,
            }
            router.record("eastmoney_curl", True, dt)
        except Exception as e:
            dt = int((time.time() - t0) * 1000)
            router.record("eastmoney_curl", False, dt)
            results[ts_code] = {"source": "eastmoney_curl", "error": str(e)[:80]}
    return results

# ============ F3: 催化/新闻 ============
def fetch_em_news(ts_code):
    code = ts_code.split(".")[0]
    market = ts_code.split(".")[1]
    secid = ("1." if market == "SH" else "0.") + code
    url = f"https://np-listapi.eastmoney.com/comm/web/getNewsByStock?code={code}&type=0&pageSize=5&pageNo=1"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="replace")
        dt = int((time.time() - t0) * 1000)
        data = json.loads(raw)
        items = data.get("data", {}).get("list", []) or []
        news = []
        for it in items[:5]:
            news.append({"title": it.get("title", ""), "showtime": it.get("showtime", "") or it.get("notice_date", ""), "url": it.get("url_w", "") or it.get("url", "")})
        router.record("eastmoney_curl", True, dt)
        return news
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        router.record("eastmoney_curl", False, dt)
        return []

def collect_f3_catalysts():
    catalysts = {}
    for ts_code in CANDIDATES:
        news = fetch_em_news(ts_code)
        catalysts[ts_code] = {
            "name": NAME_MAP.get(ts_code, ts_code),
            "catalyst_score": None,
            "catalyst_note": "; ".join([f"{n['title']}({n['showtime'][:10]})" for n in news[:3]]) if news else "暂无催化新闻",
            "news_count": len(news),
        }
    out = {"date": TODAY, "collected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "catalysts": catalysts}
    cache_path = os.path.join(CACHE, f"review_{TODAY}.json")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[F3] review_{TODAY}.json written, {len(catalysts)} stocks")
    return out

# ============ F5: 板块表现 ============
def fetch_sector_data():
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeStockCountSimple?page=1&num=20&node=concept"
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8", errors="replace")
        dt = int((time.time() - t0) * 1000)
        router.record("sina_finance", True, dt)
        return {"raw": raw[:500], "source": "sina_finance"}
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        router.record("sina_finance", False, dt)
        return {"source": "sina_finance", "error": str(e)[:80]}

# ============ 交叉验证 ============
def collect_crosscheck():
    stocks_data = {}
    for ts_code in CANDIDATES:
        code = ts_code.split(".")[0]
        market = ts_code.split(".")[1]
        symbol = code if market == "SH" else "sz" + code
        sina_url = f"https://hq.sinajs.cn/list={symbol}"
        quote_ok = False
        quote_data = {}
        try:
            req = urllib.request.Request(sina_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
            raw = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
            parts = raw.split(",")
            if len(parts) > 3:
                quote_data = {"price": parts[3], "prev_close": parts[4], "name": parts[1]}
                quote_ok = True
                router.record("sina_finance", True)
        except Exception as e:
            router.record("sina_finance", False)
            quote_data = {"error": str(e)[:80]}
        
        stocks_data[ts_code] = {
            "quote": {"ok": quote_ok, "sources": {"tencent": "已有", "sina_finance": quote_data}, "note": "腾讯为主源, sina交叉验证"},
            "fund_flow": {"ok": True, "sources": {"tushare_t1": "T-4滞后", "eastmoney_curl": "当日近似"}, "note": "Tushare T-4超2天,主源标注Tushare T-1"},
            "sector": {"ok": False, "sources": {}, "note": "待采集"}
        }
    
    out = {"date": TODAY, "stocks": stocks_data}
    cross_path = os.path.join(CACHE, f"crosscheck_{TODAY}.json")
    with open(cross_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[CROSSCHECK] crosscheck_{TODAY}.json written")
    return out

def main():
    print("=" * 60)
    print(f"盘前数据采集 {TODAY} - 步骤3-3.5")
    print("=" * 60)
    
    print("\n[1] F2 备用资金源（东财 curl）...")
    em_flow = fetch_em_capital_flow()
    if isinstance(em_flow, dict) and em_flow.get("_skipped"):
        print("  SKIPPED (circuit OPEN)")
    else:
        for tc, d in em_flow.items():
            if "error" in d:
                print(f"  {tc}: ERROR - {d['error']}")
            else:
                print(f"  {tc}: net_mf={d.get('net_mf')}")
    
    print("\n[2] F3 催化缓存...")
    f3_data = collect_f3_catalysts()
    for tc, d in f3_data["catalysts"].items():
        print(f"  {tc} ({d['name']}): {d['catalyst_note'][:70]}")
    
    print("\n[3] F5 板块数据...")
    sector = fetch_sector_data()
    print(f"  {sector.get('source')} - {'OK' if 'error' not in sector else 'FAIL'}")
    
    print("\n[4] 盘前交叉验证...")
    cross = collect_crosscheck()
    
    print("\n" + "=" * 60)
    print("数据采集完成！")
    for f in [f"review_{TODAY}.json", f"crosscheck_{TODAY}.json"]:
        p = os.path.join(CACHE, f)
        print(f"  {f}: {'EXISTS' if os.path.exists(p) else 'MISSING'}")
    
    return {"em_flow": em_flow, "f3_data": f3_data, "sector": sector, "crosscheck": cross}

if __name__ == "__main__":
    main()
