# -*- coding: utf-8 -*-
"""步骤3.6 盘前关键指标交叉验证 - 2026-09-30

对 09:15 候选（D_model_top10.csv）做三组双源对拍：
  ① 基础行情：腾讯 qt.gtimg.cn  vs  miniQMT xtdata.get_full_tick   判定「最新价差 <1%」
  ② F2 资金  ：本地 Tushare moneyflow(T-1 权威) 标注主源；当日近似源仅方向性备注
  ③ F5 板块  ：东财行业涨幅 vs 第二源（悟道盘中受限不可用 → 记 ok=false 并说明）

任一源缺失 → 记 null + ok=false，只预警不阻塞。
"""
import datetime
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import scripts.data_source_router as router
import pandas as pd

TODAY = "20260930"
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

RAW = os.path.join(HERE, "data", "premarket_20260930_raw.json")
with open(RAW, encoding="utf-8") as f:
    RAWJ = json.load(f)
RAWSTOCKS = {s["ts_code"]: s for s in RAWJ["stocks"]}
M915 = RAWJ["m915_candidates"]
F2_DATE = (RAWJ.get("f2_meta") or {}).get("date")

# Tushare 新鲜度（滞后 ≤2 天 → 视为新鲜）
fresh = False
lag = None
if F2_DATE:
    d0 = datetime.datetime.strptime(F2_DATE, "%Y%m%d").date()
    d1 = datetime.datetime.strptime(TODAY, "%Y%m%d").date()
    lag = (d1 - d0).days
    fresh = lag <= 2
print("F2 Tushare 最新日=%s 滞后=%s天 新鲜=%s" % (F2_DATE, lag, fresh))

errors = []


def fetch_miniqmt_ticks(codes):
    out = {}
    if not codes:
        return out
    if router.check("miniqmt") == "OPEN":
        print("[SKIP] miniqmt OPEN")
        return out
    t0 = time.time()
    try:
        from xtquant import xtdata
        r = xtdata.get_full_tick(codes)
        router.record("miniqmt", True, int((time.time() - t0) * 1000))
        for ts, v in (r or {}).items():
            if not v:
                continue
            out[ts] = {"last": v.get("lastPrice"), "open": v.get("open"),
                       "high": v.get("high"), "low": v.get("low"),
                       "volume": v.get("volume"), "amount": v.get("amount"),
                       "lastClose": v.get("lastClose"), "source": "miniqmt_xtdata"}
        print("[Q2] miniQMT 行情 %d/%d" % (len(out), len(codes)))
    except Exception as e:
        router.record("miniqmt", False, int((time.time() - t0) * 1000))
        errors.append("miniqmt: %s" % str(e)[:100])
        print("[ERR] miniqmt %s" % str(e)[:100])
    return out


def pct_diff(a, b):
    if a is None or b is None or a == 0:
        return None
    return round(abs(a - b) / abs(a) * 100.0, 3)


def main():
    mq = fetch_miniqmt_ticks(M915)

    stocks = {}
    for ts in M915:
        r = RAWSTOCKS.get(ts, {})
        tq_last, mq_last = r.get("last"), (mq.get(ts) or {}).get("last")

        # ① 基础行情对拍
        quote = {"tencent": tq_last, "miniqmt": mq_last,
                 "diff_pct": pct_diff(tq_last, mq_last), "ok": False,
                 "note": "", "quote_time": r.get("quote_time")}
        if tq_last is None or mq_last is None:
            quote["note"] = "缺源：tencent=%s miniqmt=%s" % (tq_last, mq_last)
        elif quote["diff_pct"] is not None and quote["diff_pct"] < 1.0:
            quote["ok"] = True
            quote["note"] = "两源一致(差%.2f%%<1%%)" % quote["diff_pct"]
        else:
            quote["note"] = "两源不一致 差%s%%" % quote["diff_pct"]

        # ② F2 资金
        f2t = r.get("f2_tushare") or {}
        f2e = r.get("f2_eastmoney") or {}
        if fresh:
            fund = {"ok": True, "primary": "tushare_T-1", "date": F2_DATE,
                    "tushare_main_net": f2t.get("main_net_inflow"),
                    "eastmoney_today_main_net": f2e.get("main_net_inflow_em"),
                    "sources": {"tushare_local_parquet": f2t.get("main_net_inflow"),
                                "eastmoney_curl": f2e.get("main_net_inflow_em")},
                    "note": "F2主源=Tushare T-1(%s)，当日近似源仅方向性备注，不同日不强校验" % F2_DATE}
        else:
            a, b = f2e.get("main_net_inflow_em"), None
            fund = {"ok": False, "primary": None,
                    "sources": {"eastmoney_curl": a},
                    "note": "Tushare 滞后>2天且第二源缺失，无法对拍"}

        # ③ F5 板块
        sec = {"eastmoney_industry_pct": r.get("industry_pct"), "industry": r.get("industry"),
               "second_source": None, "diff_pp": None, "ok": False,
               "note": "第二板块源不可用：mcp_tdx 熔断(3x list tools failed)、悟道 09:15-10:30 盘中受限、sina_finance 无 MCP 工具"}
        if r.get("industry_pct") is not None:
            sec["note"] += "；仅有东财单源 %s%%，无法对拍" % r.get("industry_pct")

        stocks[ts] = {"name": r.get("name"), "quote": quote, "fund_flow": fund, "sector": sec}

    out = {"date": TODAY, "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "target": "09:15候选(D_model_top10.csv, 数据日 %s)" % RAWJ.get("m915_data_date"),
           "f2_primary": "Tushare T-1(%s)%s" % (F2_DATE, "" if fresh else " [已超2天]"),
           "source_status": {"tencent": "ok", "miniqmt": "ok", "tushare_local": "ok",
                             "eastmoney_curl": "熔断(量比/f50 扩展字段缺失)",
                             "mcp_tdx": "熔断(3x list tools failed)",
                             "wudao": "OPEN(09:15-10:30 盘中限制, 未调用)"},
           "stocks": stocks, "errors": errors}

    p = os.path.join(CACHE, "crosscheck_%s.json" % TODAY)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    print("[OUT] %s" % p)

    qok = sum(1 for v in stocks.values() if v["quote"]["ok"])
    print("\nquote 对拍通过 %d/%d" % (qok, len(stocks)))
    for ts, v in stocks.items():
        print("  %-11s %-8s tencent=%-8s mq=%-8s diff=%-7s %s"
              % (ts, (v["name"] or "")[:6], v["quote"]["tencent"], v["quote"]["miniqmt"],
                 v["quote"]["diff_pct"], v["quote"]["note"]))


if __name__ == "__main__":
    main()
