# coding: utf-8
"""完整版复核脚本：多数据源实时行情 + 模型候选 → 完整版选股清单。

数据源优先级（由高到低）：
  1. miniQMT（本地 xtdata，实时 tick/日线，最优先）
  2. TDX（通过 MCP 采集，扩展指标最全：量比/主力资金/PE/板块涨幅）
  3. 腾讯财经免费 API（https://qt.gtimg.cn，基础行情兜底）
  4. 其他（回退标注）

哪个数据源通就用哪个；基础行情（最新价/开盘价/成交量/涨跌幅）优先 miniQMT/腾讯，
扩展指标（量比/主力资金/PE/换手率/板块涨幅）只有 TDX 能提供，TDX 不通时标注缺失、评分回退面板分。

输入：
  1) deploy_predict.py 输出的候选 CSV（data/selections/YYYYMMDD_model_topK.csv）
     提供 ts_code / model_prob / SC_F1 / SC_F4 / SC_F6（面板代理分，仅用 F1/F4/F6）
  2) 实时复核数据 data/tdx_review.json（由 AI 通过 TDX MCP 或 data_sources 采集后写入）：
     {
       "date": "20260819",
       "stocks": [
         {"ts_code":"603969.SH", "main_net_inflow":-5673766, "liangbi":0.89,
          "industry_pct":-7.0, "quote_pct":-3.66,
          "pe_ttm": 18.1, "turnover": 2.5,
          "catalyst_score":10, "catalyst_note":"半年报净利+40.35%确证利好"}
       ]
     }
     （pe_ttm=实时市盈率，turnover=实时换手率，供 F6 估值打分；缺省则 F6 回退面板分）

逻辑：
  - F2(资金) = f(实时主力净额, 量比)   （Project_15 规则；无数据回退 5.0）
  - F3(催化) = 实时 catalyst_score（AI 结合新闻/公告给出 0-10；无数据回退 0）
  - F5(板块) = f(实时所属行业当日涨幅 HYZAF)（无数据回退 5.0）
  - F1/F4 沿用 deploy 面板代理分；F6 优先用实时 PE（PE<0 或 >100 硬性扣 1 分），无实时则回退面板 SC_F6
  - 总分 = F1*25%+F2*20%+F3*20%+F4*15%+F5*10%+F6*10%（×10 得百分制）
  - 输出完整版清单 Markdown + CSV（data/selections/）

用法：
  python review_full.py --candidates data/selections/20260814_model_top5.csv
"""
import argparse
import json
import os
import time
import numpy as np
import pandas as pd

import data_sources as ds

try:
    import scripts.tushare_source as TS
except Exception:
    TS = None

HERE = os.path.dirname(os.path.abspath(__file__))
SELECT_DIR = os.path.join(HERE, "data", "selections")
DEFAULT_REVIEW = os.path.join(HERE, "data", "tdx_review.json")
# Tushare moneyflow 本地快照（每日 19:30 刷新至 T-1，P16 F2 权威口径，T-20260903 与 G2 对齐）
MF_PATH = "D:/astock/moneyflow/moneyflow.parquet"

SC_WEIGHTS = {"F1": 0.25, "F2": 0.20, "F3": 0.20, "F4": 0.15, "F5": 0.10, "F6": 0.10}


def score_f2(net_inflow, liangbi):
    """F2 资金认可度（Project_15 规则，实时主力净额 + 量比）。"""
    if np.isnan(net_inflow):
        return 5.0
    if net_inflow > 5e7 and (liangbi > 2.5 if not np.isnan(liangbi) else False):
        return 10.0
    if net_inflow > 1e7 and (liangbi > 1.5 if not np.isnan(liangbi) else False):
        return 8.0
    if net_inflow > 0:
        return 6.0
    if not np.isnan(liangbi) and liangbi >= 2:
        return 5.0  # 净流出但放量 = 分歧
    if net_inflow <= -1e8:
        return 1.0  # 净流出超1亿
    return 2.0  # 净流出缩量


def _valid_main_inflow(s, rdate):
    """F2 主力资金时效校验（T-20260831-003，2026-08-31 实锤）：
    8/31 TDX 503 → F2 降级 iFind 前一日口径，300456 用「8/28 主力净流入 +5.26 亿」打出 F2=10，
    而东财实际 8/28 为 -2.79 亿、8/31 当日 -1.25 亿 —— 方向完全相反，TOP1 名不副实（86→68 分）。
    规则：复核数据带 main_net_inflow_date 且 != 当日时，视为无当日资金（F2=5 中性分），
    绝不用旧数据/错口径打高分。数据源未提供日期戳（字段缺失）时不拦截（保持原行为）。"""
    v = s.get("main_net_inflow", np.nan)
    asof = str(s.get("main_net_inflow_date", "") or "")
    if asof and asof != str(rdate):
        code = s.get("ts_code", "")
        if not (isinstance(v, float) and np.isnan(v)):
            print(f"    !! {code} 主力资金为 {asof} 口径(非当日 {rdate})，F2 按缺失处理(5分)防旧数据虚高")
        return np.nan
    return v


def _tushare_f2_map(codes):
    """读本地 Tushare moneyflow parquet（每日 19:30 刷新至 T-1，权威四档口径），取候选最新交易日主力净额。

    主力净额 = (buy_lg_amount + buy_elg_amount - sell_lg_amount - sell_elg_amount)，Tushare 原生单位万元 → ×1e4 转元
    （与 score_f2 阈值 5e7/1e7/1e8 元口径一致，与 g2 mf_main_net 同源，零 train-serving skew）。
    返回 {ts_code: {"main_net": 元, "asof": YYYYMMDD, "source": "tushare_<asof>"}}；失败/缺失返回空 dict。"""
    if not os.path.exists(MF_PATH):
        return {}
    try:
        mf = pd.read_parquet(MF_PATH, columns=["buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"])
        mf = mf[mf.index.get_level_values("ts_code").isin(codes)]
        if mf.empty:
            return {}
        last = mf.index.get_level_values("trade_date").max()
        day = mf[mf.index.get_level_values("trade_date") == last]
        out = {}
        for code, r in day.groupby(level="ts_code"):
            r = r.iloc[-1]
            main_wan = float(r["buy_lg_amount"] + r["buy_elg_amount"] - r["sell_lg_amount"] - r["sell_elg_amount"])
            out[code] = {"main_net": main_wan * 1e4, "asof": last.strftime("%Y%m%d"), "source": "tushare_%s" % last.strftime("%Y%m%d")}
        return out
    except Exception as e:
        print("    !! tushare F2 读取失败: %s" % e)
        return {}


def _f2_value(tsh, tsh_fresh, s, rdate, code):
    """F2 主净额取值：tushare 本地快照（T-1 权威，新鲜 lag<=2）优先 → 实时复核源 → NaN。
    返回 (net_inflow_yuan_or_nan, f2_source_label)。T-20260903：与 G2 对齐 tushare 第一数据源。"""
    if tsh_fresh and code in tsh:
        return tsh[code]["main_net"], tsh[code]["source"]
    v = _valid_main_inflow(s, rdate)
    if not (isinstance(v, float) and np.isnan(v)):
        return v, "realtime"
    return np.nan, "missing"


def score_f5(industry_pct):
    """F5 板块β联动（所属行业当日涨幅）。"""
    if np.isnan(industry_pct):
        return 5.0
    if industry_pct > 3:
        return 10.0
    if industry_pct > 1.5:
        return 8.0
    if industry_pct > 0:
        return 5.0
    if industry_pct > -1:
        return 4.0
    return 2.0


def score_f6(pe_ttm, turnover):
    """F6 估值/流动性：用实时 PE-TTM + 实时换手（替代面板 asof PE，修复估值口径偏差）。

    审计修复（2026-08-21）：面板 asof PE 与实时差异大（如朗特实时 PE 789 但面板口径给 7 分），
    导致"评分高但估值畸高"的票入围。改用实时 PE 打分，PE<0 或 PE>100 硬性扣 1 分。
    """
    if np.isnan(pe_ttm):
        return 5.0
    if pe_ttm < 0 or pe_ttm > 100:
        return 1.0   # 亏损或估值畸高：硬性扣分
    if 10 <= pe_ttm <= 30 and (1 <= turnover <= 8 if not np.isnan(turnover) else True):
        return 10.0
    if 5 <= pe_ttm <= 50:
        return 7.0
    return 4.0


def _norm_stock(s):
    """把 JSON 复核数据里的 None/null 统一转成 np.nan，避免 isnan(None) 报错。"""
    out = dict(s)
    for k, v in out.items():
        if v is None:
            out[k] = np.nan
    return out


def _read_catalyst_cache(date):
    """读 9:25 集合竞价任务的 F3 催化缓存 data/cache/review_<date>.json。
    返回 {ts_code: {"catalyst_score": float, "catalyst_note": str}} 或 None（无缓存/读失败）。
    F3 催化（新闻/公告）为事件性数据、盘内变化小，9:25 采集缓存、9:45 缺失时兜底；
    F2 主力资金 / F5 板块涨幅保持 9:45 实时（分档敏感、分钟级波动）。"""
    path = os.path.join(HERE, "data", "cache", "review_%s.json" % date)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        cats = d.get("catalysts")
        if not isinstance(cats, dict):
            return None
        return cats
    except Exception:
        return None


def _read_crosscheck(date):
    """读 9:25 集合竞价任务的盘前交叉验证 data/cache/crosscheck_<date>.json。
    返回 {ts_code: {"fund_flow": {...}, "quote": {...}, "sector": {...}}} 或 None。
    用于对多源不一致的关键指标预警（防 F2 类数据源口径事故，T-20260831-003）。"""
    path = os.path.join(HERE, "data", "cache", "crosscheck_%s.json" % date)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("stocks") or None
    except Exception:
        return None


def _crosscheck_warns(cc, code):
    """汇总某候选交叉验证不一致项，返回预警列表（如 ["F2资金:两源方向不一致"]）。"""
    item = (cc or {}).get(code)
    if not item:
        return []
    warns = []
    for key, label in (("fund_flow", "F2资金"), ("quote", "行情"), ("sector", "F5板块")):
        sub = item.get(key) or {}
        if sub.get("ok") is False:
            warns.append("%s:%s" % (label, sub.get("note") or "多源不一致"))
    return warns


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="deploy_predict 输出的候选 CSV 路径")
    ap.add_argument("--review", default=DEFAULT_REVIEW, help="实时复核 JSON 路径（TDX/多数据源统一格式）")
    ap.add_argument("--threshold", type=float, default=58.0, help="评分卡红线")
    args = ap.parse_args()

    cand = pd.read_csv(args.candidates, dtype={"ts_code": str})
    cand["ts_code"] = cand["ts_code"].str.strip()

    # 读取复核数据（TDX 优先；缺失时自动回退多数据源）
    if os.path.exists(args.review):
        review = json.load(open(args.review, encoding="utf-8"))
    else:
        review = {"date": time.strftime("%Y%m%d"), "stocks": []}
    rdate = str(review.get("date", time.strftime("%Y%m%d")))
    stocks = {s["ts_code"]: _norm_stock(s) for s in review["stocks"]}

    # F3 催化缓存兜底（9:25 集合竞价任务写 data/cache/review_<date>.json）
    # 仅当实时复核缺失 catalyst（字段缺/NaN）时用缓存；显式 0=真实无催化，不动。
    cache = _read_catalyst_cache(rdate)
    if cache:
        n_f3 = 0
        for code, s in stocks.items():
            c = cache.get(code)
            if not c:
                continue
            cur = s.get("catalyst_score")
            if cur is None or (isinstance(cur, float) and np.isnan(cur)):
                s["catalyst_score"] = c.get("catalyst_score", 0.0)
                s["catalyst_note"] = c.get("catalyst_note", "")
                src = str(s.get("source", "") or "")
                s["source"] = (src + "+cache") if src else "cache"
                n_f3 += 1
        if n_f3:
            print(f"  [F3缓存] 从 9:25 缓存补 catalyst {n_f3} 只 (data/cache/review_{rdate}.json)")

    # 检查缺失 → 多数据源回退
    missing_codes = [r["ts_code"] for _, r in cand.iterrows() if r["ts_code"] not in stocks]
    if missing_codes:
        print(f"  !! {len(missing_codes)} 只候选无复核数据，尝试多数据源回退: {missing_codes}")
        fallback = ds.fetch_all(missing_codes)
        errors = fallback.pop("_errors", [])
        for code, s in fallback.items():
            stocks[code] = {
                "ts_code": code,
                "name": s.get("name", ""),
                "last": s.get("last"),
                "open": s.get("open"),
                "close": s.get("close"),
                "volume": s.get("volume"),
                "quote_pct": s.get("quote_pct"),
                "main_net_inflow": s.get("main_net_inflow", np.nan),
                "liangbi": s.get("liangbi", np.nan),
                "industry_pct": s.get("industry_pct", np.nan),
                "pe_ttm": s.get("pe_ttm", np.nan),
                "turnover": s.get("turnover", np.nan),
                "catalyst_score": s.get("catalyst_score", 0.0),
                "catalyst_note": s.get("catalyst_note", ""),
                "source": s.get("source", ""),
            }
        if errors:
            print(f"  回退数据源错误: {errors}")
        else:
            print(f"  回退成功: {set(s.get('source','') for s in stocks.values() if s.get('source'))}")

    print(f"[1/3] 候选 {len(cand)} 只 + 复核数据 {rdate} (来源: {set(s.get('source','') for s in stocks.values())})")

    # F2 主源 = Tushare moneyflow 本地快照（T-1 权威口径，每日 19:30 刷新；新鲜 lag<=2 天作为主源）
    tsh_map = _tushare_f2_map(list(cand["ts_code"]))
    tsh_fresh = False
    if tsh_map:
        asof = pd.to_datetime(next(iter(tsh_map.values()))["asof"], format="%Y%m%d")
        lag = (pd.Timestamp.now().normalize() - asof).days
        tsh_fresh = lag <= 2
        if tsh_fresh:
            print(f"  [F2主源] Tushare moneyflow 本地快照 {asof.date()}（滞后 {lag} 天 ≤2）→ 权威口径主源")
        else:
            print(f"  [F2主源] Tushare 快照滞后 {lag} 天 >2 → 回退实时源")

    # T-20260903 三源补充（全部 fail-safe，失败回退现有逻辑）：
    #   F5 = 申万一级行业 T-1 自算（本地 sw_l1 成分 + 增量库收盘）权威
    #   F6 = Tushare daily_basic T-1（交叉验证 + 实时缺失时权威兜底）
    #   F3 = Tushare forecast/express 业绩公告（实时/9:25缓存均缺失时兜底）
    sw_map, db_map, ea_map = {}, {}, {}
    if TS is not None:
        try:
            asof_t = rdate
            if "trade_date" in cand.columns and len(cand):
                asof_t = str(cand["trade_date"].iloc[0]).strip()
            _codes = list(cand["ts_code"])
            try:
                sw_map = TS.sw_l1_pct_map(_codes, asof_t)
                if sw_map:
                    print("  [F5主源] 申万一级行业 T-1 自算（%s）: %s" % (
                        next(iter(sw_map.values()))["asof"],
                        ", ".join("%s=%s%.2f%%" % (c, v["sw_l1"], v["pct"]) for c, v in sw_map.items())))
            except Exception as e:
                print("  !! sw_l1 行业读取失败: %s" % e)
            try:
                db_map = TS.daily_basic_map(_codes, asof_t)
                if db_map:
                    print("  [F6校验] Tushare daily_basic %s 覆盖 %d/%d 只" % (asof_t, len(db_map), len(_codes)))
            except Exception as e:
                print("  !! daily_basic 读取失败: %s" % e)
            try:
                ea_map = TS.earnings_anns_map(_codes, asof_t)
                if ea_map:
                    print("  [F3公告] Tushare 业绩预告/快报覆盖 %d 只: %s" % (len(ea_map), ", ".join(sorted(ea_map))))
            except Exception as e:
                print("  !! forecast/express 读取失败: %s" % e)
        except Exception as e:
            print("  !! Tushare 三源取数失败（整体降级）: %s" % e)

    # 盘前交叉验证标注（9:25 写 data/cache/crosscheck_<date>.json，防 F2 类口径事故）
    cc = _read_crosscheck(rdate)
    if cc:
        cc_bad = sum(1 for c in cand["ts_code"] if _crosscheck_warns(cc, c))
        if cc_bad:
            print(f"  [交叉验证] {cc_bad} 只候选存在指标不一致，详见明细")

    rows = []
    for _, r in cand.iterrows():
        code = r["ts_code"]
        s = stocks.get(code)
        if s is None:
            print(f"    !! 候选 {code} 无任何数据源可用，跳过")
            continue
        cwarns = _crosscheck_warns(cc, code) if cc else []
        if cwarns:
            print(f"    !! [交叉验证] {code} {'; '.join(cwarns)}")
        main_net, f2_src = _f2_value(tsh_map, tsh_fresh, s, rdate, code)
        f2 = score_f2(main_net, s.get("liangbi", np.nan))
        # F3 催化：实时 > 9:25缓存（上面已回填）> Tushare 业绩公告兜底（T-20260903）
        f3 = float(s.get("catalyst_score", 0.0))
        f3_note = s.get("catalyst_note", "")
        f3_src = ""
        if (np.isnan(f3) or f3 == 0.0) and not f3_note:
            ea = ea_map.get(code)
            if ea:
                f3 = float(ea["score"])
                f3_note = ea["note"]
                f3_src = "tushare_anns"
        if np.isnan(f3):
            f3 = 0.0
        # F5 板块：申万一级行业 T-1 权威（新鲜则优先）> 实时 industry_pct 兜底
        sw = sw_map.get(code)
        if sw is not None and "pct" in sw:
            f5 = score_f5(sw["pct"])
            f5_src = "sw_l1_%s" % sw["asof"]
            ind_used = sw["pct"]
            ind_label = "%s(%s)" % (sw["sw_l1"], f5_src)
        else:
            f5 = score_f5(s.get("industry_pct", np.nan))
            f5_src = "realtime"
            ind_used = s.get("industry_pct", np.nan)
            ind_label = "实时行业"
        # F6 估值：实时 PE 优先；缺失时 Tushare daily_basic T-1 权威兜底；两者都在做交叉验证
        pe_ttm = s.get("pe_ttm", np.nan)
        turnover = s.get("turnover", np.nan)
        tcb_warn = ""
        db = db_map.get(code)
        if db is not None:
            tpe = db.get("pe_ttm", np.nan)
            if not np.isnan(tpe):
                if np.isnan(pe_ttm):
                    pe_ttm = tpe
                    if np.isnan(turnover):
                        turnover = db.get("turnover_rate", np.nan)
                else:
                    try:
                        ratio = abs(float(pe_ttm) - float(tpe)) / max(abs(float(tpe)), 1e-9)
                        if ratio > 0.5 or (float(pe_ttm) < 0) != (float(tpe) < 0):
                            tcb_warn = "F6校验:实时PE(%.1f) vs Tushare T-1(%.1f)差异大" % (pe_ttm, tpe)
                    except Exception:
                        pass
        f1 = float(r["SC_F1"])
        f4 = float(r["SC_F4"])
        f6 = score_f6(pe_ttm, turnover) if not np.isnan(pe_ttm) else float(r["SC_F6"])
        total = sum(SC_WEIGHTS[k] * v for k, v in {"F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6}.items()) * 10.0
        rows.append({
            "ts_code": code, "name": s.get("name", ""),
            "quote_pct": s.get("quote_pct", np.nan), "model_prob": float(r["model_prob"]),
            "F1": f1, "F2": f2, "F3": f3, "F4": f4, "F5": f5, "F6": f6,
            "total": round(total, 1),
            "pe_ttm": pe_ttm,
            "main_net_inflow": s.get("main_net_inflow", np.nan),
            "liangbi": s.get("liangbi", np.nan),
            "industry_pct": s.get("industry_pct", np.nan),
            "catalyst_note": f3_note,
            "source": s.get("source", ""),
            "f2_source": f2_src,
            "f5_source": f5_src,
            "ind_used": ind_used,
            "ind_label": ind_label,
            "crosscheck_warn": "; ".join(cwarns),
            "tushare_crosscheck": tcb_warn,
        })

    out = pd.DataFrame(rows).sort_values("total", ascending=False).reset_index(drop=True)
    print("[2/3] 完整版评分：")
    show = out[["ts_code", "name", "quote_pct", "model_prob", "F1", "F2", "F3", "F4", "F5", "F6", "total"]].copy()
    show["quote_pct"] = show["quote_pct"].round(2)
    show["model_prob"] = show["model_prob"].round(3)
    show["pass"] = np.where(out["total"] >= args.threshold, "✅", "")
    print(show.to_string(index=False))

    print("[3/3] 输出完整版清单 ...")
    date_str = rdate
    os.makedirs(SELECT_DIR, exist_ok=True)
    csv_path = os.path.join(SELECT_DIR, f"{date_str}_selection_full.csv")
    md_path = os.path.join(SELECT_DIR, f"{date_str}_selection_full.md")
    out.to_csv(csv_path, index=False, encoding="utf-8-sig")

    lines = [
        f"# 双轨选股 · 完整版清单（多数据源实时复核）",
        "",
        f"> 生成时间：{rdate}  |  数据口径：模型分基于面板特征；F2/F3/F5 为实时数据（TDX 优先，缺失回退面板分）；F1/F4/F6 为面板代理分",
        f"> 评分卡红线：{args.threshold:.0f} 分",
        f"> 数据来源：{', '.join(sorted(set(s.get('source','') for s in stocks.values() if s.get('source'))))}",
        "",
        "| 排名 | 代码 | 名称 | 当日涨跌 | F1位置 | F2资金 | F3催化 | F4技术 | F5板块 | F6估值 | 总分 | 模型分 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, (_, r) in enumerate(out.iterrows(), 1):
        pct = f"{r['quote_pct']:.2f}%" if not np.isnan(r["quote_pct"]) else "—"
        p = "✅" if r["total"] >= args.threshold else "⚠️"
        lines.append(
            f"| {i} | {r['ts_code']} | {r['name']} | {pct} | {r['F1']:.0f} | {r['F2']:.0f} | {r['F3']:.0f} "
            f"| {r['F4']:.0f} | {r['F5']:.0f} | {r['F6']:.0f} | {r['total']:.1f} | {r['model_prob']:.3f} | {p} |"
        )
    lines += ["", "## 实时复核明细"]
    for i, (_, r) in enumerate(out.iterrows(), 1):
        net = f"{r['main_net_inflow']/1e4:.0f} 万" if not np.isnan(r["main_net_inflow"]) else "—"
        ind = f"{r['ind_used']:.2f}%" if not np.isnan(r["ind_used"]) else "—"
        lb = f"{r['liangbi']:.2f}" if not np.isnan(r["liangbi"]) else "—"
        src = f" (来源: {r['source']})" if r.get("source") else ""
        lines += [
            f"### {i}. {r['ts_code']} {r['name']}（总分 {r['total']:.1f}）{src}",
            f"- F2 资金：今日主力净额 **{net}**，量比 {lb} → {r['F2']:.0f} 分（来源: {r.get('f2_source','')}）",
            f"- F3 催化：{r['catalyst_note'] or '—'} → {r['F3']:.0f} 分",
            f"- F5 板块：{r['ind_label']} **{ind}** → {r['F5']:.0f} 分",
        ]
        if r.get("tushare_crosscheck"):
            lines.append(f"- ⚠️ {r['tushare_crosscheck']}")
        lines.append(f"- ⚠️ 盘前交叉验证：{r['crosscheck_warn']}" if r.get("crosscheck_warn") else "- 盘前交叉验证：无异常")
        lines.append("")
    lines += [
        "---",
        "> ⚠️ 免责声明：本清单为模型 + 多数据源实时数据复核的研究信号，不构成投资建议。",
    ]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("    CSV:", csv_path)
    print("    MD :", md_path)


if __name__ == "__main__":
    main()
