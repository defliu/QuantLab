# coding: utf-8
"""A 股在线数据补充工具包 (astock_kit)

来源: github.com/simonlin1212/a-stock-data (SKILL.md V3.6.0, Apache-2.0)
定位: 提供 D:/astock 离线买断资产之外、策略当日/实时需要的在线补充数据，
      也可作为现有在线取数(腾讯/QMT/P16 em_direct)的独立第二实现与备用源。

覆盖数据域:
  行情      tencent_quote(腾讯 PE/PB/市值/换手/涨跌停) / tdx_client+tdx_kline(mootdx 可选)
  估值/财务  eastmoney_stock_info / sina_financial_report(三表) / sina_adjust_factor(复权因子)
  龙虎榜    dragon_tiger_board(个股+席位+机构) / daily_dragon_tiger(全市场) / dragon_tiger_backup(备胎)
  资金面    margin_trading(两融) / block_trade(大宗) / holder_num_change(股东户数)
           / dividend_history(分红) / stock_fund_flow_120d(120日资金流)
           / eastmoney_fund_flow_minute(分钟资金流) / fund_flow_backup(新浪备胎)
  解禁/板块  lockup_expiry(限售解禁) / industry_comparison(行业排名)
           / board_fund_flow(板块资金流) / eastmoney_concept_blocks(板块归属)
  涨停池    em_zt_pool / em_zb_pool / em_dt_pool / em_yzt_pool
  研报      eastmoney_reports
  新闻      eastmoney_stock_news / cls_telegraph / eastmoney_global_news
  公告      cninfo_announcements / announcements_backup
  热点      ths_hot_reason(同花顺强势股+题材归因)

设计约束:
  1) 零第三方依赖(仅标准库 urllib/json/re/datetime); mootdx 可选(import 失败则相关函数报错)
  2) 所有 eastmoney.com 请求统一走 em_get() 串行限流(最小间隔+抖动), 防 IP 风控
  3) 返回 list[dict]/dict, 与现有 reader 风格一致; 网络/接口错误返回 [] 或抛 ValueError(参数错)
  4) 免费在线接口无 SLA, 宜作补充/备用源, 不能替代 D:/astock 历史全量(PIT 财务/2009 起日线)

用法示例:
    from data.astock_kit import tencent_quote, daily_dragon_tiger, margin_trading
    q = tencent_quote(["600519"])            # 实时估值
    lhb = daily_dragon_tiger("2026-08-31")   # 全市场龙虎榜
    m = margin_trading("600519")             # 融资融券日级
"""
import json
import random
import re
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_MIN_INTERVAL = 1.0  # 东财请求最小间隔(秒), 批量任务建议调大到 1.5~2

# ---------------- 基础工具 ----------------

_TICKER_RE = re.compile(r"^(?:([A-Za-z]{2})?(\d{6})(?:\.([A-Za-z]{2}))?|(bj|sh|sz)(\d{6}))$")


def get_prefix(code: str) -> str:
    """6 位代码 → 市场前缀(sh/sz/bj)。92 号段(北交所)必须先于 9x 判断。"""
    c = str(code).strip()
    if c.startswith(("92", "8")) or c[:2] in ("43", "83", "87"):
        return "bj"
    if c.startswith(("6", "9", "5")):
        return "sh"
    return "sz"


def norm_ticker(code: str) -> str:
    """任意写法(600519/SH600519/600519.SH/bj920982) → 纯 6 位数字。不匹配抛 ValueError。"""
    raw = str(code).strip()
    m = _TICKER_RE.match(raw)
    if not m:
        raise ValueError("无法把 %r 解析为 6 位股票代码(支持 600519/SH600519/600519.SH)" % code)
    return m.group(2) or m.group(5)


def em_market_code(code: str) -> int:
    """东财 secid 市场号: 沪=1, 深/北=0。不可用 startswith('6') 判市场(会误判 51x ETF/588x)。"""
    return 1 if get_prefix(code) == "sh" else 0


def em_secid(code: str) -> str:
    """东财 push2/push2his 的 secid, 如 1.600519 / 0.300750。"""
    return "%d.%s" % (em_market_code(code), norm_ticker(code))


_DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Cache-Control": "no-cache",
}


def _http_get(url, timeout=15, headers=None, retries=3):
    """通用 GET, 返回 bytes。对瞬态断开/5xx 做指数退避重试(东财住宅 IP 间歇风控更稳)。"""
    h = dict(_DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise
            last_err = e
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
        if attempt < retries - 1:
            time.sleep(0.6 * (2 ** attempt) + random.uniform(0.1, 0.3))
    raise last_err


def _http_post(url, data, timeout=15, headers=None):
    """通用 POST(form), 返回 bytes。"""
    h = dict(_DEFAULT_HEADERS)
    if headers:
        h.update(headers)
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ---------------- 东财统一请求(限流防封) ----------------

_em_last_call = [0.0]


def em_get(url, params=None, timeout=15, headers=None):
    """东财统一请求入口: 串行限流(最小间隔+随机抖动), 复用 UA。
    所有 eastmoney.com 请求都应走这里, 避免高频被封 IP。"""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    wait = EM_MIN_INTERVAL - (time.time() - _em_last_call[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.05, 0.3))
    _h = {"Referer": "https://quote.eastmoney.com/", "Origin": "https://quote.eastmoney.com"}
    if headers:
        _h.update(headers)
    try:
        return _http_get(url, timeout=timeout, headers=_h)
    finally:
        _em_last_call[0] = time.time()


def eastmoney_datacenter(report_name, columns="ALL", filter_str="", page_size=50,
                         sort_columns="", sort_types="-1"):
    """东财数据中心统一查询 — 龙虎榜/解禁/两融/大宗/股东户数/分红 共用。"""
    params = {
        "reportName": report_name, "columns": columns, "filter": filter_str,
        "pageNumber": "1", "pageSize": str(page_size), "sortColumns": sort_columns,
        "sortTypes": sort_types, "source": "WEB", "client": "WEB",
    }
    d = json.loads(em_get(DATACENTER_URL, params).decode("utf-8"))
    return (d.get("result") or {}).get("data") or []


def _datacenter_all(report_name, filter_str="", sort_columns="", sort_types="-1",
                    page_size=500, max_pages=20):
    """东财数据中心全量翻页查询(不传 filter 拉全市场一版)。
    返回原始行 list[dict]; 取满 result.count 自动停。"""
    rows = []
    for pn in range(1, max_pages + 1):
        params = {"reportName": report_name, "columns": "ALL", "filter": filter_str,
                  "pageNumber": str(pn), "pageSize": str(page_size),
                  "sortColumns": sort_columns, "sortTypes": sort_types,
                  "source": "WEB", "client": "WEB"}
        d = json.loads(em_get(DATACENTER_URL, params).decode("utf-8"))
        result = d.get("result") or {}
        data = result.get("data") or []
        total = result.get("count") or 0
        rows.extend(data)
        if not data or (total and len(rows) >= total):
            break
    return rows


def daily_margin_market():
    """全市场当日融资融券(最新一版)。返回 [{date, market, code, name, rzye, rzmre, rqye, rzrqye}]。
    数据源 RPTA_WEB_RZRQ_GGMX 按 DATE 倒序翻页, 只保留最新交易日的行。"""
    rows = _datacenter_all("RPTA_WEB_RZRQ_GGMX", sort_columns="DATE", sort_types="-1")
    latest = str(rows[0].get("DATE", ""))[:10] if rows else ""
    return [{"date": latest, "market": r.get("MARKET", ""), "code": r.get("SCODE", ""),
             "name": r.get("SECNAME", ""), "rzye": r.get("RZYE", 0), "rzmre": r.get("RZMRE", 0),
             "rqye": r.get("RQYE", 0), "rzrqye": r.get("RZRQYE", 0)}
            for r in rows if str(r.get("DATE", ""))[:10] == latest]


def latest_holder_market():
    """全市场各股最新一期股东户数(每行带各自报告期)。返回 [{date, code, name, holder_num, pre_holder_num, change_ratio}]。
    数据源 RPT_HOLDERNUMLATEST 按 END_DATE 倒序翻页, 返回全部(各股最新披露)。"""
    rows = _datacenter_all("RPT_HOLDERNUMLATEST", sort_columns="END_DATE", sort_types="-1")
    return [{"date": str(r.get("END_DATE", ""))[:10], "code": r.get("SECURITY_CODE", ""),
             "name": r.get("SECURITY_NAME_ABBR", ""), "holder_num": r.get("HOLDER_NUM", 0),
             "pre_holder_num": r.get("PRE_HOLDER_NUM", 0),
             "change_ratio": r.get("HOLDER_NUM_RATIO", 0)}
            for r in rows]


def daily_block_market():
    """全市场当日大宗交易。返回 [{date, code, name, price, close, premium_pct, vol, amount, buyer, seller}]。
    数据源 RPT_DATA_BLOCKTRADE 按 TRADE_DATE 倒序翻页, 只保留最新交易日。"""
    rows = _datacenter_all("RPT_DATA_BLOCKTRADE", sort_columns="TRADE_DATE", sort_types="-1")
    latest = str(rows[0].get("TRADE_DATE", ""))[:10] if rows else ""
    out = []
    for r in rows:
        if str(r.get("TRADE_DATE", ""))[:10] != latest:
            continue
        close = r.get("CLOSE_PRICE") or 0
        deal = r.get("DEAL_PRICE") or 0
        out.append({"date": latest, "code": r.get("SECURITY_CODE", ""),
                    "name": r.get("SECURITY_NAME_ABBR", ""), "price": deal, "close": close,
                    "premium_pct": round((deal / close - 1) * 100, 2) if close else 0,
                    "vol": r.get("DEAL_VOLUME", 0), "amount": r.get("DEAL_AMT", 0),
                    "buyer": r.get("BUYER_NAME", ""), "seller": r.get("SELLER_NAME", "")})
    return out


def upcoming_lockup_market(trade_date=None, forward_days=90):
    """全市场未来 N 天限售解禁。返回 [{date, code, name, type, shares, ratio}]。
    数据源 RPT_LIFT_STAGE, filter 收窄到 [today, today+forward]。"""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)).strftime("%Y-%m-%d")
    rows = _datacenter_all("RPT_LIFT_STAGE",
                           filter_str="(FREE_DATE>='%s')(FREE_DATE<='%s')" % (trade_date, end),
                           sort_columns="FREE_DATE", sort_types="1", max_pages=10)
    return [{"date": str(r.get("FREE_DATE", ""))[:10], "code": r.get("SECURITY_CODE", ""),
             "name": r.get("SECURITY_NAME_ABBR", ""), "type": r.get("FREE_SHARES_TYPE", ""),
             "shares": r.get("FREE_SHARES", 0), "ratio": r.get("FREE_RATIO", 0)}
            for r in rows]


def today_reports_market(date=None):
    """全市场当日研报(不带 code)。返回 [{publishDate, orgSName, title, emRatingName, predictThisYearEps}]。"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    url = "https://reportapi.eastmoney.com/report/list"
    all_records = []
    for page in range(1, 11):
        params = {"industryCode": "*", "pageSize": "100", "industry": "*",
                  "rating": "*", "ratingChange": "*", "beginTime": date, "endTime": date,
                  "pageNo": str(page), "fields": "", "qType": "0", "orgCode": "",
                  "code": "", "rcode": "", "p": str(page), "pageNum": str(page),
                  "pageNumber": str(page)}
        try:
            d = json.loads(em_get(url, params, timeout=30).decode("utf-8"))
        except Exception:
            break
        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
        time.sleep(0.3)
    return [{"publishDate": r.get("publishDate", ""), "orgSName": r.get("orgSName", ""),
             "title": r.get("title", ""), "emRatingName": r.get("emRatingName", ""),
             "predictThisYearEps": r.get("predictThisYearEps"),
             "predictNextYearEps": r.get("predictNextYearEps"),
             "predictNextTwoYearEps": r.get("predictNextTwoYearEps")} for r in all_records]


# ---------------- 行情层 ----------------

def tencent_quote(codes):
    """腾讯财经实时行情(不封 IP)。支持个股/指数/ETF。
    codes: ["600519","000001","510050","000300"]
    返回 {code: {name, price, last_close, open, change_pct, high, low,
                amount_wan, turnover_pct, pe_ttm, pe_static, pb,
                mcap_yi, float_mcap_yi, limit_up, limit_down}}"""
    prefixed = []
    key_of = {}
    for c in codes:
        low = str(c).lower()
        if low.startswith(("sh", "sz", "bj")):
            p = low
        elif c.startswith("92"):
            p = "bj%s" % c
        elif c.startswith(("5", "6", "9")):
            p = "sh%s" % c
        elif c.startswith(("4", "8")):
            p = "bj%s" % c
        else:
            p = "sz%s" % c
        prefixed.append(p)
        key_of[p] = c
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    data = _http_get(url, headers={"User-Agent": "Mozilla/5.0"}).decode("gbk", "ignore")
    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        result[key_of.get(key, code)] = {
            "name": vals[1],
            "price": float(vals[3]) if vals[3] else 0,
            "last_close": float(vals[4]) if vals[4] else 0,
            "open": float(vals[5]) if vals[5] else 0,
            "change_pct": float(vals[32]) if vals[32] else 0,
            "high": float(vals[33]) if vals[33] else 0,
            "low": float(vals[34]) if vals[34] else 0,
            "amount_wan": float(vals[37]) if vals[37] else 0,
            "turnover_pct": float(vals[38]) if vals[38] else 0,
            "pe_ttm": float(vals[39]) if vals[39] else 0,
            "mcap_yi": float(vals[44]) if vals[44] else 0,
            "float_mcap_yi": float(vals[45]) if vals[45] else 0,
            "pb": float(vals[46]) if vals[46] else 0,
            "limit_up": float(vals[47]) if vals[47] else 0,
            "limit_down": float(vals[48]) if vals[48] else 0,
            "pe_static": float(vals[52]) if vals[52] else 0,
        }
    return result


# --- mootdx 通达信(可选依赖) ---

def tdx_client(market="std"):
    """创建 mootdx 通达信客户端, 规避 0.11.x BESTIP 空串 bug + 坏服务器静默空表。
    逐候选 TCP 探测 + 真实取数验活, 失败回退 bestip / 裸 factory。
    需先 pip install mootdx; 未安装时抛 ImportError。"""
    try:
        from mootdx.quotes import Quotes
    except ImportError:
        raise ImportError("mootdx 未安装, 无法使用通达信行情源。pip install mootdx")
    servers = [
        ("119.97.185.59", 7709), ("124.70.133.119", 7709), ("116.205.183.150", 7709),
        ("123.60.73.44", 7709), ("116.205.163.254", 7709), ("121.36.225.169", 7709),
        ("123.60.70.228", 7709), ("124.71.9.153", 7709), ("110.41.147.114", 7709),
        ("124.71.187.122", 7709),
    ]
    import socket

    def _probe(ip, port, timeout=2.0):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except Exception:
            return False

    def _validate(client):
        try:
            df = client.bars(symbol="000001", frequency=9, offset=1)
            return df is not None and not df.empty
        except Exception:
            return False

    for ip, port in servers:
        if not _probe(ip, port):
            continue
        try:
            c = Quotes.factory(market=market, server=(ip, port))
            if _validate(c):
                return c
        except Exception:
            continue
    for kwargs in ({"bestip": True}, {}):
        try:
            c = Quotes.factory(market=market, **kwargs)
            if _validate(c):
                return c
        except Exception:
            continue
    raise RuntimeError("所有通达信服务器均无法取到数据(TCP 可达但返回空/被 reset)")


def tdx_kline(code, frequency=9, offset=10):
    """通达信 K 线。frequency: 0=5分 1=15分 2=30分 3=60分 4=日 5=周 6=月 8=1分 9=日(默认)。
    返回 list[dict]: {datetime, open, close, high, low, vol, amount} (不复权)。"""
    client = tdx_client()
    df = client.bars(symbol=norm_ticker(code), frequency=frequency, offset=offset)
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def tdx_quotes(codes):
    """通达信实时报价(五档盘口, 46 字段)。返回 list[dict]。"""
    client = tdx_client()
    q = client.quotes(symbol=[norm_ticker(c) for c in codes])
    if q is None or q.empty:
        return []
    return q.to_dict("records")


# ---------------- 估值 / 财务 ----------------

def eastmoney_stock_info(code):
    """东财个股基本面: 行业/总股本/流通股/市值/上市日期/现价。
    注意: push2 子域对部分住宅 IP 有间歇连接级风控, 被断时返回 {} 请用 tencent_quote 回退估值。"""
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {"fltt": "2", "invt": "2",
              "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
              "secid": em_secid(code)}
    try:
        d = json.loads(em_get(url, params).decode("utf-8")).get("data") or {}
    except Exception:
        return {}
    return {
        "code": d.get("f57", ""), "name": d.get("f58", ""),
        "industry": d.get("f127", ""), "total_shares": d.get("f84", 0),
        "float_shares": d.get("f85", 0), "mcap": d.get("f116", 0),
        "float_mcap": d.get("f117", 0), "list_date": str(d.get("f189", "")),
        "price": d.get("f43", 0),
    }


def sina_financial_report(code, report_type="lrb", num=8):
    """新浪财报三表。report_type: fzb(资产负债表)/lrb(利润表)/llb(现金流量表)。
    返回按报告期倒序的记录列表(每期一条 dict, 键为中文科目名, 值字符串/数字)。"""
    paper_code = get_prefix(code) + norm_ticker(code)
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {"paperCode": paper_code, "source": report_type, "type": "0",
              "page": "1", "num": str(num)}
    j = json.loads(_http_get(url + "?" + urllib.parse.urlencode(params)).decode("utf-8"))
    report_list = ((j.get("result") or {}).get("data") or {}).get("report_list") or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        obj = report_list[period]
        rec = {"报告期": "%s-%s-%s" % (period[:4], period[4:6], period[6:8])}
        for it in obj.get("data", []) or []:
            rec[it.get("item_title", "")] = it.get("item_value", "")
        rows.append(rec)
    return rows


def sina_adjust_factor(code, kind="qfq"):
    """新浪复权因子序列, 按日期倒序。kind: qfq(前复权, 因子为除数)/hfq(后复权, 因子为乘数)。"""
    if kind not in ("qfq", "hfq"):
        raise ValueError("kind 只能是 qfq 或 hfq")
    digits = norm_ticker(code)
    prefix = get_prefix(digits)
    symbol = "%s%s" % (prefix, digits)
    url = "https://finance.sina.com.cn/realstock/company/%s/%s.js" % (symbol, kind)
    text = _http_get(url, headers={"Referer": "https://finance.sina.com.cn/"}).decode("gbk", "ignore")
    brace = text.find("{")
    if brace < 0:
        raise RuntimeError("新浪复权因子响应无 JSON(%s/%s): %s" % (symbol, kind, text[:120]))
    data, _ = json.JSONDecoder().raw_decode(text[brace:])
    return [{"date": it["d"], "factor": float(it["f"])} for it in data.get("data", [])]


# ---------------- 龙虎榜 ----------------

def dragon_tiger_board(code, trade_date, look_back=30):
    """龙虎榜聚合: 近 look_back 日上榜记录 + 最新上榜买卖席位 TOP5 + 机构动向。"""
    start = datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=look_back)
    records = []
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str="(TRADE_DATE>='%s')(TRADE_DATE<='%s')(SECURITY_CODE=\"%s\")"
                   % (start.strftime("%Y-%m-%d"), trade_date, code),
        page_size=50, sort_columns="TRADE_DATE", sort_types="-1")
    for row in data:
        records.append({"date": str(row.get("TRADE_DATE", ""))[:10],
                        "reason": row.get("EXPLANATION", ""),
                        "net_buy_wan": round((row.get("BILLBOARD_NET_AMT") or 0) / 10000, 1),
                        "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2)})
    buy_data, sell_data = [], []
    seats = {"buy": [], "sell": []}
    institution = {"buy_amt": 0, "sell_amt": 0, "net_amt": 0}
    if records:
        latest = records[0]["date"]
        buy_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSBUY",
            filter_str="(TRADE_DATE='%s')(SECURITY_CODE=\"%s\")" % (latest, code),
            page_size=10, sort_columns="BUY", sort_types="-1")
        sell_data = eastmoney_datacenter(
            "RPT_BILLBOARD_DAILYDETAILSSELL",
            filter_str="(TRADE_DATE='%s')(SECURITY_CODE=\"%s\")" % (latest, code),
            page_size=10, sort_columns="SELL", sort_types="-1")
        for row in buy_data[:5]:
            seats["buy"].append({"name": row.get("OPERATEDEPT_NAME", ""),
                                 "buy_wan": round((row.get("BUY") or 0) / 10000, 1),
                                 "sell_wan": round((row.get("SELL") or 0) / 10000, 1)})
        for row in sell_data[:5]:
            seats["sell"].append({"name": row.get("OPERATEDEPT_NAME", ""),
                                  "buy_wan": round((row.get("BUY") or 0) / 10000, 1),
                                  "sell_wan": round((row.get("SELL") or 0) / 10000, 1)})
    for detail_data, side in [(buy_data, "buy"), (sell_data, "sell")]:
        for row in detail_data:
            if str(row.get("OPERATEDEPT_CODE", "")) == "0":
                amt = (row.get("BUY") or 0) if side == "buy" else (row.get("SELL") or 0)
                if side == "buy":
                    institution["buy_amt"] += amt
                else:
                    institution["sell_amt"] += amt
    institution["buy_amt"] = round(institution["buy_amt"] / 10000, 1)
    institution["sell_amt"] = round(institution["sell_amt"] / 10000, 1)
    institution["net_amt"] = round(institution["buy_amt"] - institution["sell_amt"], 1)
    return {"records": records, "seats": seats, "institution": institution}


def daily_dragon_tiger(trade_date=None, min_net_buy=None):
    """全市场龙虎榜。trade_date: YYYY-MM-DD(默认当日)。min_net_buy: 净买入下限(万元)。"""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str="(TRADE_DATE>='%s')(TRADE_DATE<='%s')" % (trade_date, trade_date),
        page_size=500, sort_columns="BILLBOARD_NET_AMT", sort_types="-1")
    if not data:
        return {"date": trade_date, "total_records": 0, "stocks": [],
                "note": "无数据(非交易日或盘后未更新)"}
    stocks = []
    for row in data:
        net_buy = (row.get("BILLBOARD_NET_AMT") or 0) / 10000
        if min_net_buy is not None and net_buy < min_net_buy:
            continue
        stocks.append({"code": row.get("SECURITY_CODE", ""), "name": row.get("SECURITY_NAME_ABBR", ""),
                       "reason": row.get("EXPLANATION", ""), "close": row.get("CLOSE_PRICE") or 0,
                       "change_pct": round(float(row.get("CHANGE_RATE") or 0), 2),
                       "net_buy_wan": round(net_buy, 1),
                       "buy_wan": round((row.get("BILLBOARD_BUY_AMT") or 0) / 10000, 1),
                       "sell_wan": round((row.get("BILLBOARD_SELL_AMT") or 0) / 10000, 1),
                       "turnover_pct": round(float(row.get("TURNOVERRATE") or 0), 2)})
    return {"date": str(data[0].get("TRADE_DATE", ""))[:10], "total_records": len(stocks),
            "stocks": stocks}


def dragon_tiger_backup(trade_date):
    """龙虎榜备胎: 深市走深交所官方结构化, 沪市走东财(均含营业部/机构)。主源被封时用。"""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")
    # 深市: 深交所官方 RPT
    body = json.dumps({"type": "3", "date": trade_date.replace("-", ""),
                       "searchkey": "", "companyCode": "", "tabid": "full"}).encode("utf-8")
    req = urllib.request.Request(
        "https://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1797_ssgs&TABKEY=tab1&PAGENO=1",
        data=body, headers={"User-Agent": UA, "Content-Type": "application/json",
                            "Referer": "https://www.szse.cn/market/stock/lhb/index.html"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        rows = ((d.get("data") or [{}])[0]).get("data") or []
        return [{"code": x.get("zqdm", ""), "name": x.get("zqjc", ""),
                 "reason": x.get("yy", ""), "date": trade_date} for x in rows]
    except Exception:
        return []


# ---------------- 资金面 / 筹码 ----------------

def margin_trading(code, page_size=30):
    """融资融券明细(日级)。返回 [{date, rzye, rzmre, rqye, rqmcl, rzrqye}], 单位元。"""
    data = eastmoney_datacenter("RPTA_WEB_RZRQ_GGMX",
                                filter_str='(SCODE="%s")' % norm_ticker(code),
                                page_size=page_size, sort_columns="DATE", sort_types="-1")
    return [{"date": str(row.get("DATE", ""))[:10], "rzye": row.get("RZYE", 0),
             "rzmre": row.get("RZMRE", 0), "rqye": row.get("RQYE", 0),
             "rqmcl": row.get("RQMCL", 0), "rzrqye": row.get("RZRQYE", 0)} for row in data]


def block_trade(code, page_size=20):
    """大宗交易记录。返回 [{date, price, close, premium_pct, vol, amount, buyer, seller}]。"""
    data = eastmoney_datacenter("RPT_DATA_BLOCKTRADE",
                                filter_str='(SECURITY_CODE="%s")' % norm_ticker(code),
                                page_size=page_size, sort_columns="TRADE_DATE", sort_types="-1")
    rows = []
    for row in data:
        close = row.get("CLOSE_PRICE") or 0
        deal = row.get("DEAL_PRICE") or 0
        rows.append({"date": str(row.get("TRADE_DATE", ""))[:10], "price": deal, "close": close,
                     "premium_pct": round((deal / close - 1) * 100, 2) if close else 0,
                     "vol": row.get("DEAL_VOLUME", 0), "amount": row.get("DEAL_AMT", 0),
                     "buyer": row.get("BUYER_NAME", ""), "seller": row.get("SELLER_NAME", "")})
    return rows


def holder_num_change(code, page_size=10):
    """股东户数变化(季度级)。返回 [{date, holder_num, change_num, change_ratio, avg_shares}]。"""
    data = eastmoney_datacenter("RPT_HOLDERNUMLATEST",
                                filter_str='(SECURITY_CODE="%s")' % norm_ticker(code),
                                page_size=page_size, sort_columns="END_DATE", sort_types="-1")
    return [{"date": str(row.get("END_DATE", ""))[:10], "holder_num": row.get("HOLDER_NUM", 0),
             "change_num": row.get("HOLDER_NUM_CHANGE", 0),
             "change_ratio": row.get("HOLDER_NUM_RATIO", 0),
             "avg_shares": row.get("AVG_FREE_SHARES", 0)} for row in data]


def dividend_history(code, page_size=20):
    """分红送转历史。返回 [{date, bonus_rmb(每股派息税前), transfer_ratio(每10股转增),
    bonus_ratio(每10股送股), plan(进度)}]。"""
    data = eastmoney_datacenter("RPT_SHAREBONUS_DET",
                                filter_str='(SECURITY_CODE="%s")' % norm_ticker(code),
                                page_size=page_size, sort_columns="EX_DIVIDEND_DATE", sort_types="-1")
    return [{"date": str(row.get("EX_DIVIDEND_DATE", ""))[:10],
             "bonus_rmb": row.get("PRETAX_BONUS_RMB", 0),
             "transfer_ratio": row.get("TRANSFER_RATIO", 0),
             "bonus_ratio": row.get("BONUS_RATIO", 0),
             "plan": row.get("ASSIGN_PROGRESS", "")} for row in data]


def stock_fund_flow_120d(code):
    """个股资金流(日级, 最近120交易日)。返回 [{date, main_net, small_net, mid_net, large_net, super_net}], 单位元。"""
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {"secid": em_secid(code), "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
              "lmt": "120"}
    try:
        d = json.loads(em_get(url, params).decode("utf-8"))
    except Exception:
        return []
    rows = []
    for line in (d.get("data") or {}).get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 7:
            rows.append({"date": parts[0],
                         "main_net": float(parts[1]) if parts[1] != "-" else 0,
                         "small_net": float(parts[2]) if parts[2] != "-" else 0,
                         "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                         "large_net": float(parts[4]) if parts[4] != "-" else 0,
                         "super_net": float(parts[5]) if parts[5] != "-" else 0})
    return rows


def eastmoney_fund_flow_minute(code):
    """个股资金流(分钟级, 当日盘中)。返回 [{time, main_net, small_net, mid_net, large_net, super_net}], 单位元。"""
    url = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
    params = {"secid": em_secid(code), "klt": 1, "fields1": "f1,f2,f3,f7",
              "fields2": "f51,f52,f53,f54,f55,f56,f57"}
    try:
        d = json.loads(em_get(url, params).decode("utf-8"))
    except Exception:
        return []
    rows = []
    for line in (d.get("data") or {}).get("klines") or []:
        parts = line.split(",")
        if len(parts) >= 6:
            rows.append({"time": parts[0], "main_net": float(parts[1]),
                         "small_net": float(parts[2]), "mid_net": float(parts[3]),
                         "large_net": float(parts[4]), "super_net": float(parts[5])})
    return rows


def fund_flow_backup(code, days=60):
    """个股资金流备胎(新浪, 东财被封时用)。返回 [{date, close, net_amount, turnover}]。"""
    pre = get_prefix(code) + norm_ticker(code)
    u = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "MoneyFlow.ssl_qsfx_zjlrqs?page=1&num=%d&sort=opendate&asc=0&daima=%s" % (days, pre))
    t = _http_get(u, headers={"Referer": "https://finance.sina.com.cn/"}).decode("utf-8", "ignore")
    arr = json.loads(t[t.index("["):t.rindex("]") + 1])
    return [{"date": x.get("opendate"), "close": x.get("trade"),
             "net_amount": x.get("netamount"), "turnover": x.get("turnover")} for x in arr]


# ---------------- 解禁 / 行业 / 板块 ----------------

def lockup_expiry(code, trade_date, forward_days=90):
    """限售解禁日历。返回 {history: [...], upcoming: [...]}。"""
    history_data = eastmoney_datacenter("RPT_LIFT_STAGE",
                                        filter_str='(SECURITY_CODE="%s")' % norm_ticker(code),
                                        page_size=15, sort_columns="FREE_DATE", sort_types="-1")
    history = [{"date": str(r.get("FREE_DATE", ""))[:10], "type": r.get("FREE_SHARES_TYPE", ""),
                "shares": r.get("FREE_SHARES", 0), "able_shares": r.get("ABLE_FREE_SHARES", 0),
                "ratio": r.get("FREE_RATIO", 0)} for r in history_data]
    end_str = (datetime.strptime(trade_date, "%Y-%m-%d") + timedelta(days=forward_days)).strftime("%Y-%m-%d")
    upcoming_data = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        filter_str='(SECURITY_CODE="%s")(FREE_DATE>=\'%s\')(FREE_DATE<=\'%s\')' % (norm_ticker(code), trade_date, end_str),
        page_size=20, sort_columns="FREE_DATE", sort_types="1")
    upcoming = [{"date": str(r.get("FREE_DATE", ""))[:10], "type": r.get("FREE_SHARES_TYPE", ""),
                 "shares": r.get("FREE_SHARES", 0), "able_shares": r.get("ABLE_FREE_SHARES", 0),
                 "ratio": r.get("FREE_RATIO", 0)} for r in upcoming_data]
    return {"history": history, "upcoming": upcoming}


def industry_comparison(top_n=20):
    """全行业涨跌幅排名(东财, ~100 行业)。返回 {top: [...], bottom: [...], total: int}。
    注意: push2 子域间歇风控时返回空。"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {"pn": "1", "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f3", "fs": "m:90+t:2",
              "fields": "f2,f3,f4,f12,f13,f14,f104,f105,f128,f136,f140,f141,f207"}
    try:
        d = json.loads(em_get(url, params).decode("utf-8"))
    except Exception:
        return {"top": [], "bottom": [], "total": 0}
    items = (d.get("data") or {}).get("diff") or []
    if not items:
        return {"top": [], "bottom": [], "total": 0}
    rows = [{"rank": i + 1, "name": it.get("f14", ""), "change_pct": it.get("f3", 0),
             "code": it.get("f12", ""), "up_count": it.get("f104", 0),
             "down_count": it.get("f105", 0), "leader": it.get("f140", ""),
             "leader_change": it.get("f136", 0)} for i, it in enumerate(items)]
    return {"top": rows[:top_n], "bottom": rows[-top_n:], "total": len(rows)}


_BOARD_FS = {"industry": "m:90+t:2", "concept": "m:90+t:3", "region": "m:90+t:1"}
_BOARD_PERIOD = {
    "today": ("f62", "f62", "f184", "f3", "f204"),
    "5d": ("f164", "f164", "f165", "f109", "f257"),
    "10d": ("f174", "f174", "f175", "f160", None),
}


def board_fund_flow(board_type="industry", period="today", top_n=20):
    """板块资金流向排名(按主力净流入降序)。
    board_type: industry/concept/region; period: today/5d/10d。
    返回 {board_type, period, total, rows:[{rank, name, code, change_pct, main_net, main_pct, leader}]}。"""
    if board_type not in _BOARD_FS:
        raise ValueError("board_type 须为 %s" % list(_BOARD_FS))
    if period not in _BOARD_PERIOD:
        raise ValueError("period 须为 %s" % list(_BOARD_PERIOD))
    fid, f_main, f_pct, f_chg, f_leader = _BOARD_PERIOD[period]
    fields = ["f12", "f14", f_chg, f_main, f_pct]
    if f_leader:
        fields.append(f_leader)
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    base = {"pz": "200", "po": "1", "np": "1", "fltt": "2", "invt": "2",
            "fid": fid, "fs": _BOARD_FS[board_type],
            "fields": ",".join(dict.fromkeys(fields))}
    try:
        d = json.loads(em_get(url, {**base, "pn": "1"}).decode("utf-8"))
    except Exception:
        return {"board_type": board_type, "period": period, "total": 0, "rows": []}
    items = (d.get("data") or {}).get("diff") or []
    rows = []
    for i, it in enumerate(items[:top_n]):
        r = {"rank": i + 1, "name": it.get("f14", ""), "code": it.get("f12", ""),
             "change_pct": it.get(f_chg, 0), "main_net": it.get(f_main, 0),
             "main_pct": it.get(f_pct, 0)}
        if f_leader:
            r["leader"] = it.get(f_leader, "")
        rows.append(r)
    return {"board_type": board_type, "period": period, "total": len(items), "rows": rows}


def eastmoney_concept_blocks(code):
    """东财 slist 个股所属板块(行业/概念/地域混合)。返回 [{name, code, change_pct, leader}]。"""
    url = "https://push2.eastmoney.com/api/qt/slist/get"
    params = {"spt": "3", "fltt": "2", "invt": "2", "secid": em_secid(code),
              "fields": "f12,f14,f2,f3,f4,f62,f104,f105,f128,f136,f140,f141,f207"}
    try:
        d = json.loads(em_get(url, params).decode("utf-8"))
    except Exception:
        return []
    items = (d.get("data") or {}).get("diff") or []
    return [{"name": it.get("f14", ""), "code": it.get("f12", ""),
             "change_pct": it.get("f3", 0), "leader": it.get("f140", "")} for it in items]


# ---------------- 涨停池 (打板层) ----------------

ZTB_UT = "7eea3edcaed734bea9cbfc24409ed989"


def _fmt_zt_time(t):
    if not t:
        return ""
    t = int(t)
    return "%02d:%02d" % (t // 60, t % 60) if t < 10000 else str(t)


def _em_zt_api(endpoint, sort, date):
    """东财涨停板行情中心通用请求(push2ex)。endpoint: getTopicZTPool/ZBPool/DTPool/YesterdayZTPool。"""
    url = "https://push2ex.eastmoney.com/" + endpoint
    params = {"ut": ZTB_UT, "dpt": "wz.ztzt", "Pageindex": 0,
              "pagesize": 10000, "sort": sort, "date": date}
    try:
        d = json.loads(em_get(url, params).decode("utf-8"))
        return (d.get("data") or {}).get("pool") or []
    except Exception:
        return []


def em_zt_pool(date):
    """涨停池。date=YYYYMMDD(交易日)。返回 [{code,name,price,pct,float_cap,turnover,limit_days,
    first_seal,last_seal,seal_fund,break_times,industry,zt_stat}]。"""
    out = []
    for p in _em_zt_api("getTopicZTPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "float_cap": p["ltsz"],
                    "turnover": round(p["hs"], 2), "limit_days": p["lbc"],
                    "first_seal": _fmt_zt_time(p["fbt"]), "last_seal": _fmt_zt_time(p["lbt"]),
                    "seal_fund": p["fund"], "break_times": p["zbc"],
                    "industry": p.get("hybk", ""),
                    "zt_stat": "%s天%s板" % ((p.get("zttj") or {}).get("days", "?"), (p.get("zttj") or {}).get("ct", "?"))})
    return out


def em_zb_pool(date):
    """炸板池(涨停后开板)。"""
    out = []
    for p in _em_zt_api("getTopicZBPool", "fbt:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "limit_price": p["ztp"] / 1000, "pct": round(p["zdp"], 2),
                    "turnover": round(p["hs"], 2), "first_seal": _fmt_zt_time(p["fbt"]),
                    "break_times": p["zbc"], "amplitude": round(p["zf"], 2),
                    "industry": p.get("hybk", "")})
    return out


def em_dt_pool(date):
    """跌停池。"""
    out = []
    for p in _em_zt_api("getTopicDTPool", "fund:asc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
                    "seal_fund": p["fund"], "last_seal": _fmt_zt_time(p["lbt"]),
                    "dt_days": p.get("days"), "open_times": p.get("oc"),
                    "industry": p.get("hybk", "")})
    return out


def em_yzt_pool(date):
    """昨日涨停池(昨涨停今表现)。"""
    out = []
    for p in _em_zt_api("getYesterdayZTPool", "zs:desc", date):
        out.append({"code": p["c"], "name": p["n"], "price": p["p"] / 1000,
                    "pct": round(p["zdp"], 2), "turnover": round(p["hs"], 2),
                    "amplitude": round(p["zf"], 2), "y_first_seal": _fmt_zt_time(p.get("yfbt")),
                    "y_limit_days": p["ylbc"], "industry": p.get("hybk", "")})
    return out


# ---------------- 研报 ----------------

def eastmoney_reports(code, max_pages=5):
    """东财个股研报列表。返回 [{publishDate, orgSName, title, emRatingName,
    predictThisYearEps, predictNextYearEps, predictNextTwoYearEps}]。"""
    url = "https://reportapi.eastmoney.com/report/list"
    all_records = []
    for page in range(1, max_pages + 1):
        params = {"industryCode": "*", "pageSize": "100", "industry": "*",
                  "rating": "*", "ratingChange": "*", "beginTime": "2000-01-01",
                  "endTime": "2030-01-01", "pageNo": str(page), "fields": "",
                  "qType": "0", "orgCode": "", "code": norm_ticker(code), "rcode": "",
                  "p": str(page), "pageNum": str(page), "pageNumber": str(page)}
        try:
            d = json.loads(em_get(url, params, timeout=30).decode("utf-8"))
        except Exception:
            break
        rows = d.get("data") or []
        if not rows:
            break
        all_records.extend(rows)
        if page >= (d.get("TotalPage", 1) or 1):
            break
        time.sleep(0.3)
    return [{"publishDate": r.get("publishDate", ""), "orgSName": r.get("orgSName", ""),
             "title": r.get("title", ""), "emRatingName": r.get("emRatingName", ""),
             "predictThisYearEps": r.get("predictThisYearEps"),
             "predictNextYearEps": r.get("predictNextYearEps"),
             "predictNextTwoYearEps": r.get("predictNextTwoYearEps")} for r in all_records]


# ---------------- 新闻 ----------------

def eastmoney_stock_news(code, page_size=20):
    """东财个股新闻。返回 [{title, content, time, source, url}]。"""
    cb = "jQuery_news"
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    inner = json.dumps({"uid": "", "keyword": norm_ticker(code), "type": ["cmsArticleWebOld"],
                        "client": "web", "clientType": "web", "clientVersion": "curr",
                        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default",
                                                      "pageIndex": 1, "pageSize": page_size,
                                                      "preTag": "", "postTag": ""}}},
                       separators=(",", ":"))
    params = {"cb": cb, "param": inner}
    try:
        text = em_get(url, params, headers={"Referer": "https://so.eastmoney.com/"}).decode("utf-8", "ignore")
    except Exception:
        return []
    try:
        d = json.loads(text[text.index("(") + 1:text.rindex(")")])
    except Exception:
        return []
    rows = []
    for a in (d.get("result") or {}).get("cmsArticleWebOld") or []:
        rows.append({"title": re.sub(r"<[^>]+>", "", a.get("title", "")),
                     "content": re.sub(r"<[^>]+>", "", a.get("content", ""))[:200],
                     "time": a.get("date", ""), "source": a.get("mediaName", ""),
                     "url": a.get("url", "")})
    return rows


def cls_telegraph(page_size=50):
    """财联社电报(全市场实时快讯, v1 API + 本地签名, 零 key)。返回 [{title, content, time}]。"""
    import hashlib
    params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5",
              "last_time": "", "refresh_type": "1", "rn": str(page_size)}
    qs = "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
    sign = hashlib.md5(hashlib.sha1(qs.encode()).hexdigest().encode()).hexdigest()
    url = "https://www.cls.cn/v1/roll/get_roll_list?%s&sign=%s" % (qs, sign)
    try:
        d = json.loads(_http_get(url, headers={"Referer": "https://www.cls.cn/"}).decode("utf-8"))
    except Exception:
        return []
    rows = []
    for item in (d.get("data") or {}).get("roll_data") or []:
        ts = item.get("ctime")
        t = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else ""
        rows.append({"title": item.get("title", "") or item.get("brief", ""),
                     "content": item.get("content", "") or item.get("brief", ""), "time": t})
    return rows


def eastmoney_global_news(page_size=50):
    """东财全球财经资讯(7x24)。返回 [{title, summary, time}]。"""
    import uuid
    url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
    params = {"client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": "",
              "pageSize": str(page_size), "req_trace": str(uuid.uuid4())}
    try:
        d = json.loads(em_get(url, params, headers={"Referer": "https://kuaixun.eastmoney.com/"}).decode("utf-8"))
    except Exception:
        return []
    return [{"title": it.get("title", ""), "summary": (it.get("summary", "") or "")[:200],
             "time": it.get("showTime", "")} for it in (d.get("data") or {}).get("fastNewsList") or []]


# ---------------- 公告 ----------------

_CNINFO_ORGID_MAP = {}


def _cninfo_orgid(code):
    """查股票真实 orgId(巨潮 orgId 非统一 gssx0{code} 格式, 601xxx 段必须动态查)。"""
    global _CNINFO_ORGID_MAP
    if not _CNINFO_ORGID_MAP:
        try:
            j = json.loads(_http_get("http://www.cninfo.com.cn/new/data/szse_stock.json").decode("utf-8"))
            _CNINFO_ORGID_MAP = {s["code"]: s["orgId"] for s in j.get("stockList", [])}
        except Exception:
            pass
    org = _CNINFO_ORGID_MAP.get(code)
    if org:
        return org
    return "gs%s0%s" % (get_prefix(code), code)


def cninfo_announcements(code, page_size=30):
    """巨潮公告全文检索。返回 [{title, type, date, url}]。"""
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {"stock": "%s,%s" % (norm_ticker(code), _cninfo_orgid(norm_ticker(code))),
               "tabName": "fulltext", "pageSize": str(page_size), "pageNum": "1",
               "column": "", "category": "", "plate": "", "seDate": "", "searchkey": "",
               "secid": "", "sortName": "", "sortType": "", "isHLtitle": "true"}
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    try:
        d = json.loads(_http_post(url, payload, headers=headers).decode("utf-8"))
    except Exception:
        return []
    rows = []
    for item in d.get("announcements", []) or []:
        ts = item.get("announcementTime")
        date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if isinstance(ts, (int, float)) else str(ts)[:10]
        rows.append({"title": item.get("announcementTitle", ""),
                     "type": item.get("announcementTypeName", ""), "date": date,
                     "url": "https://www.cninfo.com.cn/new/disclosure/detail?annoId=%s" % item.get("announcementId", "")})
    return rows


def announcements_backup(code, page_size=20):
    """公告备胎: 深市走深交所官方, 沪市走东财。均带 PDF 直链。返回 [{title, time, pdf}]。"""
    code = norm_ticker(code)
    if code.startswith(("0", "3")):
        body = json.dumps({"channelCode": ["listedNotice_disc"], "pageSize": page_size,
                           "pageNum": 1, "stock": [code]}).encode("utf-8")
        req = urllib.request.Request(
            "https://www.szse.cn/api/disc/announcement/annList", data=body,
            headers={"User-Agent": UA, "Content-Type": "application/json",
                     "Referer": "https://www.szse.cn/disclosure/listed/notice/index.html"})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read())
        return [{"title": a.get("title"), "time": (a.get("publishTime") or "")[:10],
                 "pdf": "https://disc.static.szse.cn/download" + a.get("attachPath", "")}
                for a in d.get("data", [])]
    u = ("https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=%d"
         "&page_index=1&ann_type=A&client_source=web&stock_list=%s&f_node=0&s_node=0" % (page_size, code))
    d = json.loads(_http_get(u).decode("utf-8"))
    return [{"title": a.get("title"), "time": (a.get("notice_date") or "")[:10],
             "pdf": "https://pdf.dfcfw.com/pdf/H2_%s_1.pdf" % a.get("art_code", "")}
            for a in (d.get("data") or {}).get("list") or []]


# ---------------- 热点 / 题材归因 ----------------

def ths_hot_reason(date=None):
    """同花顺当日强势股+题材归因(reason 为人工运营标签)。date: YYYY-MM-DD, None=今天。
    返回 list[dict]: {code, name, reason, close, zhangfu, huanshou, chengjiaoe}。"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    url = ("http://zx.10jqka.com.cn/event/api/getharden/date/%s/orderby/date/"
           "orderway/desc/charset/GBK/" % date)
    try:
        d = json.loads(_http_get(url).decode("gbk", "ignore"))
    except Exception:
        return []
    if d.get("errocode", 0) != 0:
        return []
    rows = []
    for r in d.get("data") or []:
        rows.append({"code": r.get("code"), "name": r.get("name"), "reason": r.get("reason"),
                     "close": r.get("close"), "zhangfu": r.get("zhangfu"),
                     "huanshou": r.get("huanshou"), "chengjiaoe": r.get("chengjiaoe")})
    return rows


# ---------------- 统一查询入口 ----------------

def query(section, **kwargs):
    """便捷调度入口。section 取: 行情/估值/龙虎榜/两融/大宗/股东户数/分红/资金流/解禁/
    行业/板块资金流/涨停池/研报/新闻/公告/热点。参数与原函数一致。"""
    _MAP = {
        "行情": ("tencent_quote", tencent_quote),
        "估值": ("eastmoney_stock_info", eastmoney_stock_info),
        "龙虎榜": ("daily_dragon_tiger", daily_dragon_tiger),
        "两融": ("margin_trading", margin_trading),
        "大宗": ("block_trade", block_trade),
        "股东户数": ("holder_num_change", holder_num_change),
        "分红": ("dividend_history", dividend_history),
        "资金流": ("stock_fund_flow_120d", stock_fund_flow_120d),
        "解禁": ("lockup_expiry", lockup_expiry),
        "行业": ("industry_comparison", industry_comparison),
        "板块资金流": ("board_fund_flow", board_fund_flow),
        "涨停池": ("em_zt_pool", em_zt_pool),
        "研报": ("eastmoney_reports", eastmoney_reports),
        "新闻": ("eastmoney_stock_news", eastmoney_stock_news),
        "公告": ("cninfo_announcements", cninfo_announcements),
        "热点": ("ths_hot_reason", ths_hot_reason),
    }
    if section not in _MAP:
        raise ValueError("未知 section %r, 可选: %s" % (section, sorted(_MAP)))
    name, fn = _MAP[section]
    if section == "热点":
        date = kwargs.get("date")
        return fn(date if date else None)
    if section == "行情":
        return fn(kwargs.get("codes") or kwargs.get("code") or [])
    if section == "行业":
        return fn(kwargs.get("top_n", 20))
    if section == "板块资金流":
        return fn(board_type=kwargs.get("board_type", "industry"),
                  period=kwargs.get("period", "today"), top_n=kwargs.get("top_n", 20))
    code = kwargs.get("code") or kwargs.get("codes") or kwargs.get("stock")
    if section in ("龙虎榜",):
        return fn(trade_date=kwargs.get("date"), min_net_buy=kwargs.get("min_net_buy"))
    if section in ("涨停池",):
        return fn(kwargs.get("date"))
    if section in ("研报",):
        return fn(code, max_pages=kwargs.get("max_pages", 5))
    if section in ("新闻", "公告", "两融", "大宗", "股东户数", "分红", "资金流"):
        return fn(code, page_size=kwargs.get("page_size", 30)) if "page_size" in kwargs and code else fn(code)
    if section in ("估值", "解禁"):
        if section == "解禁":
            return fn(code, kwargs.get("trade_date") or kwargs.get("date"), kwargs.get("forward_days", 90))
        return fn(code)
    raise ValueError("section %r 参数不完整" % section)


if __name__ == "__main__":
    print("astock_kit 模块导入成功。可用函数: ", sorted(
        n for n in dir() if not n.startswith("_") and callable(globals()[n])))
