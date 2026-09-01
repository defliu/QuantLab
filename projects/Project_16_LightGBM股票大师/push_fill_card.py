# coding: utf-8
"""成交回报卡片推送：换仓后逐笔成交/未成交明细。

数据源：
  --source g2   → D:/QMT_POOL/g2_bridge/state/fills_<date>.json（桥 fills，含 status）
  --source v13  → data/rebalance_<date>.json（V1.3 换仓 orders，含 status）

卡片结构：摘要（成交N/未成交M/总买入额）+ 成交明细表 + 未成交清单 + 数据源/时间戳。
数据不足显示 "—"，绝不编造。

用法：
  python push_fill_card.py --date 20260902 --source g2
  python push_fill_card.py --date 20260902 --source v13 --no-send
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
G2_FILLS = r"D:/QMT_POOL/g2_bridge/state/fills_%s.json"


def _fmt(x, nd=2):
    try:
        return ("%." + str(nd) + "f") % float(x)
    except (TypeError, ValueError):
        return "—"


def _name_map():
    import csv
    d = os.path.join(PROJECT, "data", "selections")
    nm = {}
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith("_selection_full.csv"):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8-sig") as f:
                    for r in csv.DictReader(f):
                        if r.get("ts_code") and r.get("name"):
                            nm[r["ts_code"]] = r["name"]
            except Exception:
                pass
    return nm


def _load_g2(date):
    path = G2_FILLS % date
    if not os.path.exists(path):
        print("[FAIL] 无 G2 fills: %s" % path)
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    nm = _name_map()
    trades, pending = [], []
    for x in d.get("fills", []) or []:
        code = x.get("code", "")
        st = (x.get("status") or "").upper()
        item = {
            "code": code, "name": nm.get(code, ""),
            "side": "买" if x.get("action") == "BUY" else ("卖" if x.get("action") == "SELL" else x.get("action")),
            "vol": x.get("vol"), "price": x.get("price"),
            "status": st, "ts": x.get("ts", ""), "note": x.get("reason", ""),
        }
        if st == "FILLED" and int(x.get("vol") or 0) > 0:
            item["amount"] = (x.get("vol") or 0) * (x.get("price") or 0)
            trades.append(item)
        else:
            pending.append(item)
    return {"trades": trades, "pending": pending}


def _load_v13(date):
    path = os.path.join(PROJECT, "data", "rebalance_%s.json" % date)
    if not os.path.exists(path):
        print("[FAIL] 无 V1.3 rebalance: %s" % path)
        return None
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    trades, pending = [], []
    for x in d.get("orders", []) or []:
        st = (x.get("status") or "PENDING").upper()
        item = {
            "code": x.get("code", ""), "name": x.get("name", ""),
            "side": "买" if x.get("side") == "BUY" else ("卖" if x.get("side") == "SELL" else x.get("side")),
            "vol": x.get("vol"), "price": x.get("price"),
            "status": st, "ts": x.get("time", ""), "note": x.get("action", ""),
            "amount": x.get("amount"),
        }
        if st == "FILLED":
            trades.append(item)
        else:
            pending.append(item)
    return {"trades": trades, "pending": pending, "meta": {
        "hs300_pct": (d.get("market_risk") or {}).get("hs300_pct"),
        "T": (d.get("market_risk") or {}).get("T"),
        "total_buy": d.get("total_buy_amount"),
        "guard": d.get("guard_summary"),
    }}


def _build_card(data, date, source):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    trades, pending = data.get("trades", []), data.get("pending", [])
    n_buy = sum(1 for t in trades if t["side"] == "买")
    n_sell = sum(1 for t in trades if t["side"] == "卖")
    total_amount = sum(t.get("amount") or 0 for t in trades if t.get("side") == "买")

    sum_lines = ["**成交 %d 笔**（买 %d / 卖 %d）" % (len(trades), n_buy, n_sell)]
    if total_amount:
        sum_lines.append("· 买入总额 **¥%s**" % _fmt(total_amount))
    meta = data.get("meta") or {}
    if meta.get("T") is not None:
        sum_lines.append("· 大盘 T=%s（HS300 %s%%）" % (_fmt(meta.get("T"), 0), _fmt(meta.get("hs300_pct"))))
    if meta.get("guard"):
        sum_lines.append("· %s" % meta["guard"])
    if pending:
        sum_lines.append("<font color='orange'>· 未成交 %d 笔（详见下）</font>" % len(pending))

    # 成交明细表
    tbl = ["| 方向 | 代码 | 数量 | 委托价 | 金额 | 时间 |", "|---|---|---|---|---|---|"]
    for t in trades:
        tbl.append("| %s | %s %s | %s | %s | %s | %s |" % (
            "🟢买" if t["side"] == "买" else "🔴卖",
            t["code"], t["name"],
            _fmt(t.get("vol"), 0), _fmt(t.get("price")),
            _fmt(t.get("amount"), 0) if t.get("amount") else "—",
            (t.get("ts") or "")[5:16]))
    tbl_str = "\n".join(tbl)

    # 未成交清单
    pend_lines = []
    for t in pending:
        st_icon = {"CANCELED": "⛔", "REJECTED": "❌", "PENDING": "⏳"}.get(t["status"], "•")
        pend_lines.append("%s %s %s %s股@%s　%s %s" % (
            st_icon, t["side"], t["code"], _fmt(t.get("vol"), 0), _fmt(t.get("price")),
            t["status"], _clip(t.get("note"), 24)))

    elements = [{"tag": "markdown", "content": "\n".join(sum_lines)}]
    if trades:
        elements.append({"tag": "markdown", "content": "**成交明细**"})
        elements.append({"tag": "markdown", "content": tbl_str})
    if pend_lines:
        elements.append({"tag": "markdown", "content": "**未成交**"})
        elements.append({"tag": "markdown", "content": "\n".join(pend_lines)})
    elements.append({
        "tag": "markdown",
        "content": "<font color='grey'>来源：%s ｜ 数据截至 %s\n⚠️ 研究信号，不构成投资建议</font>" % (
            "G2桥fills" if source == "g2" else "V1.3 rebalance", ts),
    })

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "成交回报 · %s-%s-%s" % (date[:4], date[4:6], date[6:8])},
            "subtitle": {"tag": "plain_text", "content": "%s · Project_16 %s" % (ts, "G2" if source == "g2" else "V1.3")},
            "template": "green",
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": "成交"}, "color": "green"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def _clip(s, n=30):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser(description="成交回报卡片推送")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--source", choices=["g2", "v13"], default="g2")
    ap.add_argument("--no-send", action="store_true", help="只打印卡片 JSON，不发送")
    args = ap.parse_args()

    data = _load_g2(args.date) if args.source == "g2" else _load_v13(args.date)
    if data is None:
        sys.exit(1)
    card = _build_card(data, args.date, args.source)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        print("\n[trades] %d 笔, pending %d" % (len(data.get("trades", [])), len(data.get("pending", []))))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → %s" % ("成功" if ok else "失败", args.source))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
