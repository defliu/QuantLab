# -*- coding: utf-8 -*-
"""盘前集合竞价预判 - 多源数据采集（2026-09-16）。

采集范围：G2候选 + G2持仓 + ENS持仓
数据源路由：scripts/data_source_router.py (check/record)
"""
import json
import os
import sys
import time
import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import scripts.data_source_router as router

TODAY = datetime.date.today().strftime("%Y%m%d")
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

# 标的池
G2_CANDIDATES = ["605098.SH", "688029.SH", "688528.SH"]
G2_HOLDINGS = ["300475.SZ", "603332.SH"]
ENS_HOLDINGS = ["300138.SZ", "600409.SH"]
ALL_STOCKS = G2_CANDIDATES + G2_HOLDINGS + ENS_HOLDINGS

NAME_MAP = {
    "605098.SH": "行动教育", "688029.SH": "南微医学", "688528.SH": "秦川物联",
    "300475.SZ": "香农芯创", "603332.SH": "苏州龙杰",
    "300138.SZ": "晨光生物", "600409.SH": "三友化工",
}


def tencent_code(ts_code):
    code = ts_code.split(".")[0]
    market = ts_code.split(".")[1]
    return ("sh" if market == "SH" else "sz") + code


def fetch_tencent_quotes():
    """腾讯 API 取基础行情/竞价（价/量/量比/开盘/最高/最低）。"""
    import urllib.request
    stocks = [tencent_code(c) for c in ALL_STOCKS]
    indices = ["sh000001", "sz399001", "sz399006", "sh000300"]
    url = "https://qt.gtimg.cn/q=" + ",".join(stocks + indices)
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
        dt = int((time.time() - t0) * 1000)
        result = {}
        for line in raw.strip().split("\n"):
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            sym = key.replace("v_", "").strip()
            val = val.strip().strip(";").strip('"')
            parts = val.split("~")
            if len(parts) > 34:
                result[sym] = {
                    "name": parts[1],
                    "code": parts[2],
                    "price": parts[3],
                    "prev_close": parts[4],
                    "open": parts[5],
                    "vol": parts[6],
                    "bid_vol": parts[7],
                    "ask_vol": parts[8],
                    "pct_chg": parts[32],
                    "high": parts[33],
                    "low": parts[34],
                    "amount": parts[37] if len(parts) > 37 else "",
                    "vol_ratio": parts[38] if len(parts) > 38 else "",
                }
        router.record("tencent", True, dt)
        return result
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        router.record("tencent", False, dt)
        print("[TENCENT-ERR] %s" % e)
        return {}


def fetch_tushare_moneyflow():
    """Tushare moneyflow parquet 取 F2 主力资金（权威 T-1 口径）。"""
    import pandas as pd
    mf_path = r"D:\astock\moneyflow\moneyflow.parquet"
    t0 = time.time()
    try:
        if not os.path.exists(mf_path):
            router.record("tushare", False)
            print("[TUSHARE-ERR] moneyflow.parquet not found: %s" % mf_path)
            return {}, None
        df = pd.read_parquet(mf_path)
        # MultiIndex: (ts_code, trade_date)
        latest = str(df.index.get_level_values("trade_date").max())[:10]
        ddf = df[df.index.get_level_values("trade_date").astype(str).str[:10] == latest].reset_index()
        target_codes = set(ALL_STOCKS)
        result = {}
        for _, row in ddf.iterrows():
            code = str(row["ts_code"])
            if code in target_codes:
                net = row.get("net_mf_amount")
                buy_lg = row.get("buy_lg_amount")
                sell_lg = row.get("sell_lg_amount")
                result[code] = {
                    "trade_date": latest,
                    "net_mf_amount": float(net) if pd.notna(net) else None,
                    "buy_lg_amount": float(buy_lg) if pd.notna(buy_lg) else None,
                    "sell_lg_amount": float(sell_lg) if pd.notna(sell_lg) else None,
                }
        dt = int((time.time() - t0) * 1000)
        router.record("tushare", True, dt)
        print("[TUSHARE] moneyflow latest=%s, matched=%d/%d" % (latest, len(result), len(ALL_STOCKS)))
        return result, latest
    except Exception as e:
        dt = int((time.time() - t0) * 1000)
        router.record("tushare", False, dt)
        print("[TUSHARE-ERR] %s" % e)
        return {}, None


def fetch_eastmoney_mx_news():
    """东财 mx 取个股新闻（F3 催化）。"""
    import pandas as pd
    # 使用 mx-ds-mcp via subprocess? 这里用 MCP 工具会更方便，但脚本里调用不了 MCP
    # 退化：用 eastmoney curl 直连
    return fetch_eastmoney_curl_news()


def fetch_eastmoney_curl_news():
    """东财网页接口取个股新闻/公告（兜底 F3）。"""
    import urllib.request
    results = {}
    for ts_code in G2_CANDIDATES:
        code = ts_code.split(".")[0]
        market = ts_code.split(".")[1]
        secid = ("1." if market == "SH" else "0.") + code
        # 东财个股新闻列表 API
        url = "https://np-listapi.eastmoney.com/comm/web/getNewsByStock?code=%s&type=0&pageSize=10&pageNo=1" % code
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            raw = urllib.request.urlopen(req, timeout=12).read().decode("utf-8", errors="replace")
            dt = int((time.time() - t0) * 1000)
            data = json.loads(raw)
            items = data.get("data", {}).get("list", []) or []
            news = []
            for it in items[:8]:
                news.append({
                    "title": it.get("title", ""),
                    "showtime": it.get("showtime", "") or it.get("notice_date", ""),
                    "url": it.get("url_w", "") or it.get("url", ""),
                })
            results[ts_code] = news
            router.record("eastmoney_curl", True, dt)
        except Exception as e:
            dt = int((time.time() - t0) * 1000)
            router.record("eastmoney_curl", False, dt)
            print("[EM-CURL-ERR] %s: %s" % (ts_code, e))
    return results


def main():
    out = {"date": TODAY, "collected_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "stocks": {}}

    # 1. 腾讯行情
    print("=== 1. 腾讯行情 ===")
    quotes = fetch_tencent_quotes()
    out["quotes"] = quotes
    for sym, d in quotes.items():
        print("  %s %s 价=%s 涨跌=%s%% 开=%s 高=%s 低=%s 量比=%s" % (
            sym, d["name"], d["price"], d["pct_chg"], d["open"], d["high"], d["low"], d["vol_ratio"]))

    # 2. Tushare F2 主力资金
    print("\n=== 2. Tushare F2 主力资金 ===")
    mf, mf_date = fetch_tushare_moneyflow()
    out["moneyflow"] = mf
    out["moneyflow_date"] = mf_date
    for tc, d in mf.items():
        net = d.get("net_mf_amount")
        net_str = ("%d万" % (net / 10000)) if net is not None else "N/A"
        print("  %s 净额=%s (date=%s)" % (tc, net_str, d.get("trade_date")))

    # 3. 东财新闻/公告（F3）
    print("\n=== 3. 东财公告(F3) ===")
    news = fetch_eastmoney_curl_news()
    out["news"] = {}
    for tc, items in news.items():
        out["news"][tc] = items[:5]
        print("  %s: %d 条公告" % (tc, len(items)))
        for it in items[:3]:
            print("    - [%s] %s" % (it.get("notice_date", "")[:10], it.get("title", "")[:60]))

    # 保存
    out_path = os.path.join(CACHE, "premarket_data_%s.json" % TODAY)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[saved] %s" % out_path)
    return out


if __name__ == "__main__":
    main()
