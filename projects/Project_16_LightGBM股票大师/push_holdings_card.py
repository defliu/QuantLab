# coding: utf-8
"""持仓报告卡片推送：午休 / 盘后逐股关键指标卡。

读取任务写入的 data/cache/holdings_<date>.json（schema 见下），构建 Card 2.0：
  摘要（总浮盈亏/资金池/已实现）+ 逐股卡片（现价/涨跌/浮盈亏/主力资金/建议）+ 汇总表。

用法：
  python push_holdings_card.py --date 20260902 --type midday [--no-send]
  python push_holdings_card.py --date 20260902 --type close  [--no-send]

holdings_<date>.json schema：
  {
    "date": "20260902", "type": "midday|close",
    "capital": 100000.5, "realized_pnl": 123.4,     // 资金池 / 已实现盈亏（close 用）
    "summary": "今日表现一句话（可选）",
    "holdings": [{
      "code": "300456.SZ", "name": "赛微电子",
      "vol": 1200, "cost": 40.49, "price": 40.19,
      "quote_pct": -0.17, "pnl_pct": -0.74,          // 当日涨跌% / 浮盈亏%
      "main_wan": 720.8, "liangbi": 4.89, "sector_pct": -1.24,  // 主力净流入(万)/量比/板块涨幅%
      "catalyst": "中报净利27.02亿...", "advice": "持有|减仓|止损", "warn": "正常|警惕|高风险"
    }]
  }
数据不足显示 "—"，绝不编造。
"""
import argparse
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
CACHE_DIR = os.path.join(PROJECT, "data", "cache")

TYPE_META = {
    "midday": {"title": "午休持仓报告", "template": "blue", "tag": "午休"},
    "close": {"title": "盘后持仓复盘", "template": "indigo", "tag": "盘后"},
}


def _f(x, nd=2, sign=False):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    s = "+" if sign and v > 0 else ""
    return ("%s%." + str(nd) + "f") % (s, v)


def _f_pct(x):
    return _f(x, 2, sign=True) + "%"


def _clip(s, n=42):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _advice_color(advice):
    a = (advice or "").lower()
    if "止损" in a or "清仓" in a:
        return "red"
    if "减仓" in a or "止盈" in a:
        return "orange"
    return "green"


def _warn_color(warn):
    w = (warn or "")
    if "高风险" in w:
        return "red"
    if "警惕" in w:
        return "orange"
    return "green"


def _build_card(d, date):
    t = d.get("type", "midday")
    meta = TYPE_META.get(t, TYPE_META["midday"])
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    holdings = d.get("holdings", []) or []

    # 摘要
    sum_lines = ["**持仓 %d 只**" % len(holdings)]
    tot_pnl = 0.0
    has_pnl = False
    for h in holdings:
        try:
            tot_pnl += float(h.get("pnl_pct") or 0)
            has_pnl = True
        except (TypeError, ValueError):
            pass
    if has_pnl:
        sum_lines.append("· 平均浮盈亏 **%s**" % _f_pct(tot_pnl / max(len(holdings), 1)))
    if d.get("capital") is not None:
        sum_lines.append("· 资金池 **¥%s**" % _f(d.get("capital"), 2))
    if t == "close" and d.get("realized_pnl") is not None:
        sum_lines.append("· 已实现盈亏 **%s**" % _f(d.get("realized_pnl"), 2, sign=True))
    if d.get("summary"):
        sum_lines.append("· %s" % d["summary"])

    # 逐股卡片
    per_stock = []
    for i, h in enumerate(holdings, 1):
        warn_c = _warn_color(h.get("warn"))
        adv_c = _advice_color(h.get("advice"))
        lines = [
            "<font color='%s'>#%d %s %s</font>　浮盈亏 **%s**" % (
                warn_c, i, h.get("code", ""), h.get("name", "") or "",
                _f_pct(h.get("pnl_pct"))),
            "现价 %s 涨跌 %s ｜ 主力 %s ｜ 量比 %s ｜ 板块 %s" % (
                _f(h.get("price")), _f_pct(h.get("quote_pct")),
                _f(h.get("main_wan"), 1, sign=True) + "万" if h.get("main_wan") is not None else "—",
                _f(h.get("liangbi")), _f_pct(h.get("sector_pct"))),
            "<font color='%s'>建议：%s</font>%s" % (
                adv_c, h.get("advice") or "—",
                ("　预警：<font color='%s'>%s</font>" % (warn_c, h["warn"])) if h.get("warn") else ""),
        ]
        if h.get("catalyst"):
            lines.append("<font color='grey'>%s</font>" % _clip(h["catalyst"]))
        per_stock.append({
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": warn_c + "-100", "background_style": warn_c + "-50",
            "corner_radius": "8px", "padding": "10px", "vertical_spacing": "2px",
            "margin": "0px 0px 8px 0px",
            "elements": [{"tag": "markdown", "content": ln} for ln in lines],
        })

    # 汇总表
    tbl_rows = [[str(i), h.get("name") or h.get("code", ""),
                 _f(h.get("price")), _f_pct(h.get("pnl_pct")),
                 _f(h.get("main_wan"), 1, sign=True) + "万" if h.get("main_wan") is not None else "—",
                 h.get("advice") or "—"]
                for i, h in enumerate(holdings, 1)]
    tbl_lines = ["| 排名 | 名称 | 现价 | 浮盈亏% | 主力(万) | 建议 |",
                 "|---|---|---|---|---|---|"]
    for r in tbl_rows:
        tbl_lines.append("| " + " | ".join(r) + " |")
    tbl = "\n".join(tbl_lines)

    elements = [
        {"tag": "markdown", "content": "\n".join(sum_lines)},
    ]
    if per_stock:
        elements.append({"tag": "markdown", "content": "**逐股明细**"})
        elements.extend(per_stock)
    if tbl_rows:
        elements.append({"tag": "markdown", "content": "**汇总**"})
        elements.append({"tag": "markdown", "content": tbl})
    elements.append({
        "tag": "markdown",
        "content": "<font color='grey'>数据口径：行情/资金当日采集（F2 源降级已标注）｜ 生成 %s\n⚠️ 研究信号，不构成投资建议</font>" % ts,
    })

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "%s · %s-%s-%s" % (meta["title"], date[:4], date[4:6], date[6:8])},
            "subtitle": {"tag": "plain_text", "content": "%s · Project_16" % ts},
            "template": meta["template"],
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": meta["tag"]}, "color": "blue"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="持仓报告卡片推送（午休/盘后）")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--type", choices=["midday", "close"], default="midday")
    ap.add_argument("--no-send", action="store_true", help="只打印卡片 JSON，不发送")
    args = ap.parse_args()

    path = os.path.join(CACHE_DIR, "holdings_%s.json" % args.date)
    if not os.path.exists(path):
        print("[FAIL] 无持仓数据: %s" % path)
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("type", args.type)
    card = _build_card(d, args.date)

    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        print("\n[holdings] %d 只" % len(d.get("holdings", [])))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → %s" % ("成功" if ok else "失败", args.type))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
