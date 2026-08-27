# coding: utf-8
"""g2 实时数据采集模块（F2 主力资金 / F5 行业板块当日涨幅）。

独立于 V1.1：不 import deploy_predict.py / run_scheduled.ps1 / lgb_model_v3.txt / feature_panel_v3.parquet。
供 build_g2_daily.py / deploy_predict_g2.py 复用，把 g2 的 F2/F5 从 E:/astock 周更 parquet
换成当日实时数据，实时失败时回退周更值（诚实标注滞后）。

数据源（2026-08-25 实测）：
  F2 主力净额（当日）：新浪资金流 MoneyFlow.ssl_qsfx_lscjfb（免费、免 Key、当日已更新）
    - r0_net=超大单净额(元), r1_net=大单净额(元) -> 主力净流入 = r0_net + r1_net
    - 口径与 E:/astock moneyflow 的 buy_lg+buy_elg-sell_lg-sell_elg 一致（大单+超大单净额）
    - 单位换算：E:/astock moneyflow 单位=万元，新浪单位=元 -> 除以 1e4 对齐训练口径
    - 注意：批量 daima=a,b 实测返回空，必须逐股请求
  F5 行业板块当日涨幅：增量库当日行情 + 同花顺行业成分自算（881 板块）
    - 同花顺 881 板块指数涨幅 = 成分股当日涨跌幅按成交额加权平均（口径近似，行业分类一致）
    - 与 g2 回测 ind_pct_ths 同一行业分类体系（同花顺 881xxx），避免换行业分类造成漂移
  F5 备选：新浪 newSinaHy.php 行业平均涨幅（当日，但新浪行业分类 ≠ 同花顺 881，仅作交叉参考）

不可用源（2026-08-25 实测）：东财 push2/push2his（IP 被风控断连）、腾讯 ff_ 主力资金（none_match）、
同花顺板块实时（401 需登录）。

用法（由 build_g2_daily.py / deploy_predict_g2.py 调用）：
  from g2_realtime import fetch_main_net_sina, compute_industry_pct_daily
  mf = fetch_main_net_sina(["300684.SZ", ...])     # {code: {"mf_main_net":..., "mf_elg_net":..., "mf_main_ratio":...}}
  f5 = compute_industry_pct_daily(incr_df, comp_df)  # {ths_ind: 当日涨幅}
"""
import json
import os
import ssl
import time
import urllib.parse
import urllib.request

import numpy as np

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "Chrome/120.0.0.0 Safari/537.36")
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 新浪资金流
SINA_FF_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "MoneyFlow.ssl_qsfx_lscjfb?page=1&num=8&sort=opendate&asc=0&daima={sym}")
# 新浪行业板块（当日行业平均涨幅，备选/交叉参考）
SINA_HY_URL = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"

_MIN_INTERVAL = 0.35          # 新浪逐股请求最小间隔（秒），防限频
_last_ts = [0.0]


def _http_get(url, referer="https://finance.sina.com.cn/", timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Referer": referer})
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return r.read()


def _sleep_interval():
    wait = _MIN_INTERVAL - (time.time() - _last_ts[0])
    if wait > 0:
        time.sleep(wait)
    _last_ts[0] = time.time()


def _std_to_sina(code):
    """ts_code('300684.SZ') -> sina('sz300684')"""
    c = str(code).strip()
    if c.endswith(".SH"):
        return "sh" + c[:6]
    if c.endswith(".SZ"):
        return "sz" + c[:6]
    if c.endswith(".BJ"):
        return "bj" + c[:6]
    return c[:6]


def _sina_to_std(sym):
    """sina('sz300684') -> ts_code('300684.SZ')"""
    base = sym[-6:]
    if sym.startswith("sh"):
        return base + ".SH"
    if sym.startswith("sz"):
        return base + ".SZ"
    if sym.startswith("bj"):
        return base + ".BJ"
    return base


def fetch_main_net_sina(codes, target_date=None, max_workers=1):
    """逐股拉新浪资金流当日主力净额。

    返回 {code: {
        "mf_main_net": 万元,        # 对齐 g2 模型特征（moneyflow 五档，万元，训练口径）
        "mf_elg_net": 万元,         # 超大单净额（万元，模型特征）
        "mf_main_ratio": 占比,       # 主力净额/四档流入总额（模型特征）
        "main_net_yuan": 元,        # 对齐评分卡 F2（东财 main_net 口径，score_f2 阈值为元）
        "date": "YYYY-MM-DD"
    }}
    拉取失败/无数据 -> 该 code 不返回（调用方回退周更）。逐股请求较慢，只对候选池调用。
    """
    out = {}
    for code in codes:
        _sleep_interval()
        sym = _std_to_sina(code)
        try:
            raw = _http_get(SINA_FF_URL.format(sym=sym))
            j = json.loads(raw.decode("utf-8", "ignore"))
            if not j:
                continue
            # 找目标日，缺省取最新一日
            row = j[0]
            if target_date is not None:
                for r in j:
                    if r.get("opendate") == target_date:
                        row = r
                        break
            r0n = float(row.get("r0_net") or 0.0)
            r1n = float(row.get("r1_net") or 0.0)
            r0 = float(row.get("r0") or 0.0)
            r1 = float(row.get("r1") or 0.0)
            r2 = float(row.get("r2") or 0.0)
            r3 = float(row.get("r3") or 0.0)
            main_net_yuan = r0n + r1n                    # 元（评分卡 F2）
            main_net_w = main_net_yuan / 1e4             # 万元（模型特征）
            elg_net_w = r0n / 1e4                        # 超大单净额（万元）
            tot = r0 + r1 + r2 + r3
            ratio = (main_net_yuan / tot) if tot > 0 else np.nan
            out[code] = {
                "mf_main_net": main_net_w,
                "mf_elg_net": elg_net_w,
                "mf_main_ratio": ratio,
                "main_net_yuan": main_net_yuan,
                "date": row.get("opendate"),
            }
        except Exception:
            continue
    return out


def fetch_sina_industry_pct():
    """新浪行业板块当日平均涨幅（备选/交叉参考，非主口径）。

    返回 {行业代码: {name, pct}}，pct 为成分股平均涨跌幅(%)。
    注：新浪行业分类 ≠ 同花顺 881，仅用于参考，不直接替换 ind_pct_ths。
    """
    try:
        raw = _http_get(SINA_HY_URL)
        txt = raw.decode("gbk", "ignore")
        start = txt.find("{")
        end = txt.rfind("}")
        if start < 0 or end < 0:
            return {}
        body = txt[start + 1:end]
        out = {}
        for seg in body.split(","):
            seg = seg.strip().strip('"')
            if not seg or ":" not in seg:
                continue
            key, val = seg.split(":", 1)
            key = key.strip().strip('"')
            val = val.strip().strip('"')
            f = val.split(",")
            if len(f) >= 5:
                out[key] = {"name": f[1], "pct": float(f[4]) if _isnum(f[4]) else np.nan}
        return out
    except Exception:
        return {}


def _isnum(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def compute_industry_pct_daily(incr_df, comp_df):
    """用增量库当日行情 + 同花顺行业成分，自算当日板块涨幅（成交额加权）。

    口径：板块当日涨幅 = Σ(成分股 pct_chg × amount) / Σ(amount)，仅计未停牌且有行情、有成分映射的股票。
    与回测 ind_pct_ths 同属同花顺 881 行业分类，板块指数涨幅以成分股成交额加权近似。

    入参：
      incr_df: 增量库（含 trade_date/ts_code/close/preClose/amount/suspendFlag，已过滤到目标日）
      comp_df: 同花顺行业成分（含 股票代码/指数代码，股票代码格式 '000592.SZ'）
    返回: {ths_ind(如 '881101.TI'): 当日涨幅(%)}  (仅覆盖有成分的板块；成分不足的板块由调用方填 0)
    """
    import pandas as pd
    if incr_df is None or len(incr_df) == 0 or comp_df is None or len(comp_df) == 0:
        return {}
    df = incr_df.copy()
    df["ts_code"] = df["ts_code"].astype(str)
    # 当日涨跌幅（百分数，与 ths_daily pct_change 同量纲）
    df["pct_chg"] = (df["close"] / df["preClose"] - 1.0) * 100.0
    # 剔除停牌 / 无行情 / 非正成交额
    df = df[(df["close"].notna()) & (df["preClose"].notna()) & (df["preClose"] != 0)]
    if "suspendFlag" in df.columns:
        df = df[df["suspendFlag"] == 0]
    df["amount"] = df["amount"].fillna(0.0)
    df = df[df["amount"] > 0]

    comp = comp_df.copy()
    comp["股票代码"] = comp["股票代码"].astype(str)
    comp["指数代码"] = comp["指数代码"].astype(str)
    m = df.merge(comp[["股票代码", "指数代码"]].drop_duplicates("股票代码"),
                 left_on="ts_code", right_on="股票代码", how="inner")
    if len(m) == 0:
        return {}
    # 成交额加权平均涨幅（%）
    w = m["pct_chg"].fillna(0.0) * m["amount"]
    g = w.groupby(m["指数代码"]).sum() / m["amount"].groupby(m["指数代码"]).sum()
    return g.to_dict()


# ---------------------------------------------------------------------------
# 龙虎榜当日化（F3/lhb 特征）—— 悟道 MCP 采集后写文件，本模块只负责读取
# ---------------------------------------------------------------------------
# 采集文件规范：data/real/lhb_mcp_<YYYYMMDD>.json
#   {"date": "YYYY-MM-DD", "source": "wudao_mcp_dragon_tiger", "rows": [
#       {"stockCode": "600487", "stockName": "亨通光电", "reason": "...", "netBuy": 184959.32(万元)},
#       ...
#   ]}
# 注意：MCP 返回 netBuy 单位为【万元】，E:/astock top_list.net_amount 单位为【元】（已实测 8/21 交叉验证吻合），
#       g2 模型训练 lhb_net 用元 → 读取时 ×1e4 转元，避免量纲 bug（与 F2 同类坑）。
def _mcp_code_to_ts(code):
    """MCP 龙虎榜 stockCode('600487') -> ts_code('600487.SH')"""
    c = str(code).strip()
    if c.startswith("920"):
        return c + ".BJ"   # 北交所 920 新代码段
    if c.startswith("6") or c.startswith("9") or c.startswith("5"):
        return c + ".SH"
    if c.startswith("4") or c.startswith("8"):
        return c + ".BJ"
    return c + ".SZ"


def fetch_lhb_from_mcp_file(date_str, lhb_dir=None):
    """读取悟道 MCP 龙虎榜采集文件，聚合为 g2 特征口径。

    date_str: 'YYYY-MM-DD'（用于定位文件 lhb_mcp_YYYYMMDD.json 与校验）
    返回 {ts_code: {"lhb_net": 元, "lhb_count": int}}；文件不存在/解析失败 -> {}（调用方回退周更）。
    """
    import json as _json
    import os as _os
    if lhb_dir is None:
        lhb_dir = _os.path.join(os.getcwd(), "data", "real")
    ymd = date_str.replace("-", "")
    path = _os.path.join(lhb_dir, f"lhb_mcp_{ymd}.json")
    if not _os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            j = _json.load(f)
    except Exception:
        return {}
    rows = j.get("rows") or []
    if not rows:
        return {}
    agg = {}
    for r in rows:
        code = r.get("stockCode")
        if not code:
            continue
        ts = _mcp_code_to_ts(code)
        net = float(r.get("netBuy") or 0.0) * 1e4   # 万元 -> 元
        if ts not in agg:
            agg[ts] = {"lhb_net": 0.0, "lhb_count": 0}
        agg[ts]["lhb_net"] += net
        agg[ts]["lhb_count"] += 1
    return agg


# ---------------------------------------------------------------------------
# 龙虎榜全自动采集 —— 东财 datacenter（方案B，2026-08-25 落地）
# ---------------------------------------------------------------------------
# 背景：悟道 MCP 龙虎榜口径准但不可进自动管线；东财 datacenter 纯 Python 可全自动。
# 实证（8/21 全市场交叉验证）：E:/astock top_list 与东财 RPT_DAILYBILLBOARD_DETAILS 同源，
# 但 E:/astock 对多原因上榜股【去重保留 1 行】。去重规则已实证确定：
#   1) 按股票去重【不求和】（多原因时净额可能相同（同份数据），也可能不同）
#   2) 若该股有「连续3日/连续三个交易日 涨幅或跌幅偏离累计」记录 → 取这条（E:/astock 实测全取多日累计那条）
#   3) 否则取任一条（净额相同）
# 单位：BILLBOARD_NET_AMT 为【元】，与 g2 训练 lhb_net 一致（不需换算）。
# 零外部依赖：直接用 urllib 调东财 datacenter（不经 industry-researcher 插件，自包含）。
# Fallback 链（调用方 build_g2_daily 用）：东财当日 → MCP 文件当日 → E:/astock 周更。
_EM_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
# 多日累计偏离原因关键词（按 E:/astock 实测优先级排列：连续10日异常波动 > 连续3日累计偏离）
_LHB_MULTI_DAYS_KW = ("连续10个交易日", "连续三个交易日", "连续3个交易日", "连续3个交易日内")


def _em_http_get(url, params, timeout=15):
    """东财 datacenter GET（带 Referer，纯 urllib）。"""
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    full = url + ("&" if "?" in url else "?") + q
    req = urllib.request.Request(full, headers={
        "User-Agent": _UA,
        "Referer": "https://quote.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def fetch_lhb_eastmoney(date_str):
    """东财 datacenter 龙虎榜当日全市场（方案B，复刻 E:/astock 去重口径）。

    date_str: 'YYYY-MM-DD'
    返回 {ts_code: {"lhb_net": 元, "lhb_count": int}}；失败/无数据 -> {}（调用方回退）。
    """
    all_rows = []
    page = 1
    while True:
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILS", "columns": "ALL",
            "filter": f"(TRADE_DATE='{date_str}')",
            "pageNumber": str(page), "pageSize": "100",
            "sortColumns": "TRADE_DATE", "sortTypes": "-1",
            "source": "WEB", "client": "WEB",
        }
        try:
            j = _em_http_get(_EM_DC_URL, params)
        except Exception:
            return {}
        records = (j.get("result") or {}).get("data") or []
        all_rows += records
        if len(records) < 100:
            break
        page += 1
        if page > 20:  # 防御：最多 2000 条
            break
    if not all_rows:
        return {}

    # 按股票收集记录（不求和；多日累计偏离优先，且按关键词顺序取最高优先级那条）
    def _multi_rank(expl):
        """返回多日累计偏离的优先级；非多日累计返回 None。0 最高（连续10日异常波动）。"""
        for i, kw in enumerate(_LHB_MULTI_DAYS_KW):
            if kw in expl:
                return i
        return None

    by_code = {}
    for r in all_rows:
        code = str(r.get("SECURITY_CODE") or "")
        if not code:
            continue
        ts = _mcp_code_to_ts(code)
        net = float(r.get("BILLBOARD_NET_AMT") or 0.0)
        expl = str(r.get("EXPLANATION") or "")
        rank = _multi_rank(expl)
        if ts not in by_code:
            by_code[ts] = {"net": net, "rank": rank}
        else:
            cur = by_code[ts]
            if rank is not None and (cur["rank"] is None or rank < cur["rank"]):
                by_code[ts] = {"net": net, "rank": rank}
            elif cur["rank"] is None and rank is None:
                pass  # 非多日多条：净额相同，任取（保留首条）

    return {ts: {"lhb_net": v["net"], "lhb_count": 1} for ts, v in by_code.items()}


# ---------------------------------------------------------------------------
# 研报当日化 —— 东财 reportapi（2026-08-25 落地）
# ---------------------------------------------------------------------------
# 接口：https://reportapi.eastmoney.com/report/list（em_direct.py 已验证域名可用，纯 HTTP）
# 字段：stockCode / emRatingName（中文评级：买入/增持/持有...）
# 口径对齐：g2 训练用 {"买入":2,"增持":1,"持有":0,"中性":-1,"减持":-2,"卖出":-3}
#          东财 emRatingName 与训练同一套中文评级，直接走同一映射。
# 聚合：rc_num = 当日该股研报条数（仅计有评级的），rc_rating = 当日评级分均值（与 E:/astock 一致）。
# 分页：单日可能 >100 条（8/25 达 100 满页），循环拉全。
_RATING_MAP = {"买入": 2, "增持": 1, "持有": 0, "中性": -1, "减持": -2, "卖出": -3}
_REPORT_API = "https://reportapi.eastmoney.com/report/list"


def _report_http_get(params, timeout=15):
    """东财 reportapi GET（纯 urllib，Referer data.eastmoney.com）。"""
    q = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    full = _REPORT_API + "?" + q
    req = urllib.request.Request(full, headers={
        "User-Agent": _UA,
        "Referer": "https://data.eastmoney.com/",
        "Accept": "application/json, text/plain, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def fetch_research_eastmoney(date_str):
    """东财 reportapi 当日研报评级/数量（g2 特征口径）。

    date_str: 'YYYY-MM-DD'
    返回 {ts_code: {"rc_rating": 均值(映射分), "rc_num": 条数}}；失败/无数据 -> {}（调用方回退周更）。
    注：仅计有 emRatingName 的研报（无评级 7.4% 不纳入，与 E:/astock 行为一致）。
    """
    all_items = []
    page = 1
    while True:
        params = {
            "industryCode": "*", "pageSize": "100", "industry": "*", "rating": "*",
            "ratingChange": "*", "beginTime": date_str, "endTime": date_str,
            "pageNo": str(page), "fields": "", "qType": "0", "orgCode": "",
            "code": "*", "rcode": "",
        }
        try:
            j = _report_http_get(params)
        except Exception:
            return {}
        items = j.get("data") or []
        all_items += items
        if len(items) < 100:
            break
        page += 1
        if page > 20:  # 防御：最多 2000 条
            break
    if not all_items:
        return {}

    agg = {}
    for it in all_items:
        code = str(it.get("stockCode") or "")
        rname = str(it.get("emRatingName") or "").strip()
        if not code or rname not in _RATING_MAP:
            continue  # 无评级不纳入
        ts = _mcp_code_to_ts(code)
        if ts not in agg:
            agg[ts] = {"score": 0.0, "n": 0}
        agg[ts]["score"] += _RATING_MAP[rname]
        agg[ts]["n"] += 1

    return {ts: {"rc_rating": v["score"] / v["n"], "rc_num": v["n"]}
            for ts, v in agg.items() if v["n"] > 0}
