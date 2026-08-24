# coding: utf-8
"""东财直连模块（绕开系统代理 127.0.0.1:7897）
已验证可用域名：
  - push2ex.eastmoney.com       涨停池/打板（✅ 8/21历史可拉）
  - datacenter-web.eastmoney.com 龙虎榜/股东户数/分红/研报（✅ 历史可拉）
  - reportapi.eastmoney.com     研报（✅）
  - search-api-web.eastmoney.com 个股新闻（✅ 8/21 182条）
  - np-weblist.eastmoney.com    全球资讯（✅ 可达）
被服务器风控断开（勿用）：
  - push2.eastmoney.com / push2his.eastmoney.com  （板块/个股资金流，当前IP被断）
  - emappdata.eastmoney.com     人气榜
"""
import requests, time, json, os

EM_SESSION = requests.Session()
EM_SESSION.trust_env = False   # 关键：忽略系统/环境代理，直连
EM_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
})
EM_MIN_INTERVAL = 1.2
_em_last = [0.0]

def em_get(url, params=None, timeout=12):
    wait = EM_MIN_INTERVAL - (time.time() - _em_last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        r = EM_SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r
    finally:
        _em_last[0] = time.time()

def em_datacenter(report_name, filter_str="", columns="ALL", page_size=50,
                  sort_columns="", sort_types="-1", page_number=1):
    """东财数据中心通用查询（龙虎榜/股东户数/分红/两融/解禁等）"""
    params = {
        "reportName": report_name, "columns": columns, "filter": filter_str,
        "pageNumber": str(page_number), "pageSize": str(page_size),
        "sortColumns": sort_columns, "sortTypes": sort_types,
        "source": "WEB", "client": "WEB",
    }
    r = em_get("https://datacenter-web.eastmoney.com/api/data/v1/get", params)
    return ((r.json().get("result") or {}).get("data")) or []

# ---- 常用查询封装 ----
def lhb_detail(trade_date):
    """龙虎榜明细（按交易日）"""
    return em_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE='{trade_date}')",
        sort_columns="BILLBOARD_NET_AMT", sort_types="-1", page_size=200)

def zt_pool(trade_date, page_size=100):
    """涨停池（按日期）"""
    params = {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
              "Pageindex": "0", "pagesize": str(page_size), "sort": "fbt:asc",
              "date": trade_date}
    r = em_get("https://push2ex.eastmoney.com/getTopicZTPool", params)
    return (r.json().get("data") or {}).get("pool") or []

def holder_num(code, page_size=50):
    """股东户数（历史）"""
    return em_datacenter(
        "RPT_HOLDERNUMLATEST", filter_str=f'(SECURITY_CODE="{code}")',
        sort_columns="END_DATE", sort_types="-1", page_size=page_size)

def dividend(code, page_size=50):
    """分红送转（历史）"""
    return em_datacenter(
        "RPT_SHAREBONUS_DET", filter_str=f'(SECURITY_CODE="{code}")',
        sort_columns="EX_DIVIDEND_DATE", sort_types="-1", page_size=page_size)

def stock_news(code, page_size=10):
    """个股新闻（search-api-web）"""
    param = {"uid": "", "keyword": code, "type": ["cmsArticleWebOld"],
             "client": "web", "clientType": "web", "clientVersion": "curr",
             "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time",
                                            "pageIndex": 1, "pageSize": page_size}}}
    r = em_get("https://search-api-web.eastmoney.com/search/jsonp",
               params={"cb": "cb", "param": json.dumps(param, ensure_ascii=False)})
    txt = r.text
    if txt.startswith("cb("):
        txt = txt[3:-1]
    j = json.loads(txt)
    return (j.get("result") or {}).get("cmsArticleWebOld") or []

def report_list(begin, end, page_size=20):
    """个股研报（时间段）"""
    params = {"industryCode": "*", "pageSize": str(page_size), "industry": "*",
              "rating": "*", "ratingChange": "*", "beginTime": begin, "endTime": end,
              "pageNo": "1", "fields": "", "qType": "0", "orgCode": "",
              "code": "*", "rcode": ""}
    r = em_get("https://reportapi.eastmoney.com/report/list", params)
    return r.json().get("data") or []

if __name__ == "__main__":
    print("东财直连模块自测...", flush=True)
    print(" 龙虎榜(8/21):", [x.get("SECURITY_NAME_ABBR") for x in lhb_detail("2026-08-21")[:3]], flush=True)
    print(" 涨停池(8/21):", [x.get("n") for x in zt_pool("20260821")[:3]], flush=True)
    print(" 股东户数(002237):", [(x.get("END_DATE"), x.get("HOLDER_NUM")) for x in holder_num("002237")[:2]], flush=True)
    print(" 新闻(002237):", [x.get("title")[:30] for x in stock_news("002237", 2)], flush=True)
