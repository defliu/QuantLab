# coding: utf-8
"""昨日推荐复盘卡片推送：昨日 G2 Top2 / TOP10 今日表现 vs 大盘。

数据源：
  - 昨日候选：data/selections/<昨日>_selection_full.csv（top10）+ <昨日>_g2_top2.csv（G2 top2）
  - 今日行情：data/cache/past_quotes_<今日>.json
        {"date": "20260902", "hs300_pct": -0.26,
         "stocks": {"001309.SZ": {"last": 421.5, "pct": 1.2}, ...}}
    由 09:25 任务采集（昨日推荐股今日实时行情，腾讯 API 批量）后写入。

卡片结构：摘要（Top10 平均/Top2/跑赢跑输大盘）+ G2 Top2 表现 + TOP10 表现表 + 数据源/时间戳。
数据不足显示 "—"，绝不编造。

用法：
  python push_past_review_card.py --date 20260902
  python push_past_review_card.py --date 20260902 --no-send
"""
import argparse
import csv
import json
import os
import sys
import time

for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import qmt_card as QC

PROJECT = os.path.dirname(os.path.abspath(__file__))
SELECT_DIR = os.path.join(PROJECT, "data", "selections")
CACHE_DIR = os.path.join(PROJECT, "data", "cache")


def _fmt(x, nd=2, sign=False):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    s = "+" if sign and v > 0 else ""
    return ("%s%." + str(nd) + "f") % (s, v)


def _fmt_pct(x):
    return _fmt(x, 2, sign=True) + "%"


def _load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _find_yesterday(date):
    """找 <date> 之前最近一个 selection_full csv 的日期。"""
    best = None
    if os.path.isdir(SELECT_DIR):
        for fn in sorted(os.listdir(SELECT_DIR)):
            if fn.endswith("_selection_full.csv"):
                d = fn.split("_")[0]
                if d < date and (best is None or d > best):
                    best = d
    return best


def _load_data(date):
    y = _find_yesterday(date)
    if not y:
        print("[FAIL] 无昨日 selection_full：< %s" % date)
        return None
    # 昨日 top10（V1.3 口径）
    rows = _load_csv(os.path.join(SELECT_DIR, "%s_selection_full.csv" % y))
    rows.sort(key=lambda r: _f(r.get("total")), reverse=True)
    top10 = [{"code": r.get("ts_code", ""), "name": r.get("name", ""), "total": r.get("total")} for r in rows[:10]]
    # 昨日 G2 top2
    g2 = _load_csv(os.path.join(SELECT_DIR, "%s_g2_top2.csv" % y))
    g2 = [{"code": r.get("ts_code", ""), "name": r.get("name", ""), "total": r.get("score_total", r.get("total"))} for r in g2]
    # 今日行情
    qp = os.path.join(CACHE_DIR, "past_quotes_%s.json" % date)
    if not os.path.exists(qp):
        print("[FAIL] 无今日行情：%s（先由 09:25 任务写）" % qp)
        return None
    with open(qp, encoding="utf-8") as f:
        q = json.load(f)
    return {"yesterday": y, "top10": top10, "g2": g2, "quotes": q.get("stocks", {}), "hs300_pct": q.get("hs300_pct")}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _build_card(data, date):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    y = data["yesterday"]
    qs, hs = data["quotes"], data["hs300_pct"]

    def pct(code):
        s = qs.get(code)
        if not s or s.get("pct") is None:
            return None
        return float(s["pct"])

    # 摘要统计
    pcts = [pct(r["code"]) for r in data["top10"]]
    pcts = [p for p in pcts if p is not None]
    avg10 = sum(pcts) / len(pcts) if pcts else None
    g2_pcts = [pct(r["code"]) for r in data["g2"] if pct(r["code"]) is not None]
    avg2 = sum(g2_pcts) / len(g2_pcts) if g2_pcts else None

    sum_lines = ["**昨日（%s）推荐 · 今日表现**" % y]
    if avg10 is not None:
        beat = (avg10 - hs) if hs is not None else None
        btxt = ("（%s大盘 %s%%）" % ("跑赢" if beat >= 0 else "跑输", _fmt_pct(hs))) if beat is not None else ""
        sum_lines.append("· TOP10 平均 **%s** %s" % (_fmt_pct(avg10), btxt))
    if avg2 is not None:
        sum_lines.append("· G2 Top2 平均 **%s**" % _fmt_pct(avg2))
    if hs is None:
        sum_lines.append("· （未提供大盘 HS300 涨跌）")

    # G2 Top2 表现
    g2_lines = ["| 昨日 | 代码 | 名称 | 今涨跌% |", "|---|---|---|---|"]
    for r in data["g2"]:
        g2_lines.append("| %s分 | %s | %s | %s |" % (
            _fmt(r.get("total"), 1), r["code"], r["name"] or "—",
            _fmt_pct(pct(r["code"])) if pct(r["code"]) is not None else "—"))

    # TOP10 表现表
    t10_lines = ["| 排名 | 代码 | 名称 | 昨分 | 今涨跌% |", "|---|---|---|---|---|"]
    for i, r in enumerate(data["top10"], 1):
        p = pct(r["code"])
        mark = "🟢" if (p is not None and hs is not None and p >= hs) else ("🔴" if (p is not None and hs is not None) else "")
        t10_lines.append("| %d | %s | %s | %s | %s %s |" % (
            i, r["code"], r["name"] or "—", _fmt(r.get("total"), 1),
            _fmt_pct(p) if p is not None else "—", mark))

    elements = [{"tag": "markdown", "content": "\n".join(sum_lines)}]
    if data["g2"]:
        elements.append({"tag": "markdown", "content": "**G2 Top2 今日表现**"})
        elements.append({"tag": "markdown", "content": "\n".join(g2_lines)})
    if data["top10"]:
        elements.append({"tag": "markdown", "content": "**TOP10 今日表现**　<font color='grey'>🟢跑赢大盘 🔴跑输</font>"})
        elements.append({"tag": "markdown", "content": "\n".join(t10_lines)})
    elements.append({
        "tag": "markdown",
        "content": "<font color='grey'>今日行情：09:25 采集（腾讯 API）｜ 大盘 HS300 %s ｜ 数据截至 %s\n⚠️ 研究信号，不构成投资建议</font>" % (
            _fmt_pct(hs) if hs is not None else "—", ts),
    })

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "昨日推荐复盘 · %s-%s-%s" % (date[:4], date[4:6], date[6:8])},
            "subtitle": {"tag": "plain_text", "content": "%s · 校准信号可信度" % ts},
            "template": "turquoise",
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": "复盘"}, "color": "blue"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="昨日推荐复盘卡片推送")
    ap.add_argument("--date", required=True, help="今日 YYYYMMDD")
    ap.add_argument("--no-send", action="store_true", help="只打印卡片 JSON，不发送")
    args = ap.parse_args()

    data = _load_data(args.date)
    if data is None:
        sys.exit(1)
    card = _build_card(data, args.date)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → 昨日复盘" % ("成功" if ok else "失败"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
