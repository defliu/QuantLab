# coding: utf-8
"""盘后总览卡片推送：15:45 G2 对账后推一张总览（V1.3 持仓 + G2 对账/成交/持仓合一）。

数据源：
  - V1.3 持仓：data/cache/holdings_<date>.json（15:40 盘后任务写入）
  - G2 成交：D:/QMT_POOL/g2_bridge/state/fills_<date>.json
  - G2 对账摘要：--summary 传入（reconcile_g2.py 输出）

用法：
  python push_daily_summary_card.py --date 20260902 --summary "G2对账:无差额|无孤儿"
  python push_daily_summary_card.py --date 20260902 --no-send
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
from qmt_bridge_client import fetch_price

PROJECT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT, "data", "cache")
G2_FILLS = r"D:/QMT_POOL/g2_bridge/state/fills_%s.json"

# V1.3 持仓真实源（gen_positions_cfg_v13.py 每日 09:00 生成，成本含费/除权调整）
V13_CMD_DIR = r"D:/QMT_POOL/p16_v13_risk/cmd"
V13_ACCOUNT = "67014907"


def _load_v13(date):
    """读 V1.3 真实持仓源 positions_cfg_v13_<date>.json（回退最近一份），
    构造 holdings[{code,vol,cost,pnl_pct}] + capital（成本合计）。
    修根因（T-20260922-005）：此前读无人写入的 data/cache/holdings_<date>.json → 永远"无持仓数据"。"""
    try:
        files = sorted([f for f in os.listdir(V13_CMD_DIR)
                        if f.startswith("positions_cfg_v13_") and f.endswith(".json")])
    except Exception:
        files = []
    pos = []
    # 精确命中当日；否则回退最近一份（当日文件未生成时）
    for target in ["positions_cfg_v13_%s.json" % date] + list(reversed(files)):
        try:
            d = json.load(open(os.path.join(V13_CMD_DIR, target), encoding="utf-8"))
        except Exception:
            continue
        if str(d.get("account_id", "")) == V13_ACCOUNT:
            pos = d.get("positions", []) or []
            break
    holdings = []
    for p in pos:
        code, vol, cost = p.get("code"), int(p.get("vol", 0) or 0), float(p.get("cost", 0) or 0)
        if not code or vol <= 0 or cost <= 0:
            continue
        price = fetch_price(code)
        pnl = ((price / cost - 1) * 100) if (price and price > 0) else 0.0
        holdings.append({"code": code, "vol": vol, "cost": cost, "pnl_pct": pnl})
    capital = sum(h["cost"] * h["vol"] for h in holdings)
    return {"holdings": holdings, "capital": capital, "date": date}


def _fmt(x, nd=2, sign=False):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    s = "+" if sign and v > 0 else ""
    return ("%s%." + str(nd) + "f") % (s, v)


def _f_pct(x):
    return _fmt(x, 2, sign=True) + "%"


def _clip(s, n=40):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _load(date):
    v13 = _load_v13(date)
    g2 = {"fills": [], "trades": []}
    p = G2_FILLS % date
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            g2 = json.load(f)
        g2["trades"] = [x for x in (g2.get("fills") or []) if (x.get("status") or "").upper() == "FILLED" and int(x.get("vol") or 0) > 0]
    return {"v13": v13, "g2": g2}


def _build_card(data, date, summary):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    v13, g2 = data["v13"], data["g2"]

    lines = []
    # V1.3 概览
    hs = v13.get("holdings", []) or []
    tot = 0.0
    for h in hs:
        try:
            tot += float(h.get("pnl_pct") or 0)
        except (TypeError, ValueError):
            pass
    if hs:
        lines.append("**V1.3（67014907）**　持仓 %d 只，平均浮盈 %s，成本合计 ¥%s" % (
            len(hs), _f_pct(tot / len(hs)), _fmt(v13.get("capital"))))
    else:
        lines.append("**V1.3（67014907）**　（空仓或无持仓源）")
    # G2 概览
    trades = g2.get("trades", [])
    g2_filled_buy = sum(1 for t in trades if t.get("action") == "BUY")
    g2_filled_sell = sum(1 for t in trades if t.get("action") == "SELL")
    lines.append("**G2（70180771）**　成交 %d 笔（买%d/卖%d）" % (len(trades), g2_filled_buy, g2_filled_sell))
    for seg in (summary or "").split("|"):
        seg = seg.strip()
        if seg:
            lines.append("· %s" % seg)

    # G2 成交表
    tbl = ["| 方向 | 代码 | 数量 | 价格 | 状态 |", "|---|---|---|---|---|"]
    for t in g2.get("fills", []) or []:
        tbl.append("| %s | %s | %s | %s | %s |" % (
            "买" if t.get("action") == "BUY" else ("卖" if t.get("action") == "SELL" else t.get("action")),
            t.get("code", ""), _fmt(t.get("vol"), 0), _fmt(t.get("price")),
            t.get("status", "")))

    elements = [{"tag": "markdown", "content": "\n".join(lines)}]
    if g2.get("fills"):
        elements.append({"tag": "markdown", "content": "**G2 成交明细**"})
        elements.append({"tag": "markdown", "content": "\n".join(tbl)})
    elements.append({
        "tag": "markdown",
        "content": "<font color='grey'>盘后总览 · 数据截至 %s\n⚠️ 研究信号，不构成投资建议</font>" % ts,
    })

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "盘后总览 · %s-%s-%s" % (date[:4], date[4:6], date[6:8])},
            "subtitle": {"tag": "plain_text", "content": "%s · V1.3 + G2" % ts},
            "template": "blue",
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [{"tag": "text_tag", "text": {"tag": "plain_text", "content": "总览"}, "color": "blue"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def _read_summary(args):
    """取对账摘要，返回 (summary, err)。--summary-file 优先。"""
    if args.summary_file:
        if not os.path.exists(args.summary_file):
            return "", "摘要文件不存在: %s" % args.summary_file
        try:
            with open(args.summary_file, encoding="utf-8") as f:
                txt = f.read()
        except Exception as e:
            return "", "摘要文件读取失败: %r" % (e,)
        if not txt.strip():
            return "", "摘要文件为空: %s" % args.summary_file
        return txt, ""
    return args.summary or "", ""


def main():
    ap = argparse.ArgumentParser(description="盘后总览卡片推送")
    ap.add_argument("--date", required=True)
    ap.add_argument("--summary", default="", help="G2 对账摘要，用 | 分隔")
    ap.add_argument("--summary-file", default=None,
                    help="从 UTF-8 文件读对账摘要（长文本安全通道）")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    summary, serr = _read_summary(args)
    if serr:
        print("[FAIL] %s" % serr)
        sys.exit(3)
    data = _load(args.date)
    card = _build_card(data, args.date, summary)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → 盘后总览" % ("成功" if ok else "失败"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
