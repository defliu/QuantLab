# coding: utf-8
"""G2 盘前卡片推送：TOP10 打分明细（速览 + 六因子）+ Top5 重点卡。

两种模式：
  - 丰富版（09:50 复核后）：读 data/selections/<date>_selection_full.csv
      （F1-F6 实时打分 + 当日主力净流入 + 量比 + 板块 + 催化备注）
  - 预估版（09:25 预判后）：读 data/selections/<date>_model_top10.csv（SC_F1-F6 模型预估分）
      + data/cache/crosscheck_<date>.json（当日主力资金/现价/板块）
      + data/cache/review_<date>.json（F3 催化）

用法：
  python push_review_card.py --date 20260902 --summary "上证-0.06%|..." --action "买入:001309/600267|卖出:600028|仓位:正常" --holdings "600028:200|601988:200"   # 丰富版+发送
  python push_review_card.py --date 20260902 --estimate --summary "..."    # 预估版+发送
  python push_review_card.py --date 20260902 --no-send                     # 只打印卡片 JSON 不发送

卡片结构：📌操作建议置顶 + 摘要 + Top3重点卡(含查看行情按钮/降级标注) + TOP10速览表 + 六因子表 + 数据源/时间戳。
数据不足时对应字段显示 "—"，绝不编造。
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

F_COLS = ["F1", "F2", "F3", "F4", "F5", "F6"]
F_LABELS = {"F1": "位置", "F2": "资金", "F3": "催化", "F4": "技术", "F5": "板块", "F6": "估值"}


def _fmt_wan(yuan):
    """元 → 万（带符号，>=1亿 不约分）。"""
    try:
        v = float(yuan) / 1e4
    except (TypeError, ValueError):
        return "—"
    s = "+" if v > 0 else ""
    if abs(v) >= 10000:
        return "%s%.0f万" % (s, v)
    return "%s%.1f万" % (s, v)


def _fmt_pct(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    s = "+" if v > 0 else ""
    return "%s%.2f%%" % (s, v)


def _fmt_num(x, nd=2):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    return ("%." + str(nd) + "f") % v


def _fval(fs, key):
    """取数值因子/总分，缺失返回 None（不做格式化）。"""
    try:
        v = float(fs.get(key))
    except (TypeError, ValueError):
        return None
    return v


def _f(fs, key, nd=0):
    v = _fval(fs, key)
    if v is None:
        return "—"
    return ("%." + str(nd) + "f") % v


def _clip(s, n=46):
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _name_map():
    """从已有 selection_full.csv 汇总 代码→名称（名称不随日变化）。"""
    nm = {}
    if not os.path.isdir(SELECT_DIR):
        return nm
    for fn in sorted(os.listdir(SELECT_DIR)):
        if not fn.endswith("_selection_full.csv"):
            continue
        for r in _load_csv(os.path.join(SELECT_DIR, fn)):
            if r.get("ts_code") and r.get("name"):
                nm[r["ts_code"]] = r["name"]
    return nm


# ---------------------------------------------------------------------------
# 数据装配
# ---------------------------------------------------------------------------

def _stocks_rich(date):
    """丰富版：selection_full.csv → 统一 stock 列表（按总分降序）。"""
    rows = _load_csv(os.path.join(SELECT_DIR, "%s_selection_full.csv" % date))
    out = []
    for r in rows:
        if not r.get("ts_code"):
            continue
        out.append({
            "rank": 0,
            "code": r.get("ts_code", ""),
            "name": r.get("name", ""),
            "price": _fmt_num(r.get("quote_pct"), 2) if r.get("quote_pct") not in (None, "") else None,
            "quote_pct": r.get("quote_pct"),
            "main_wan": r.get("main_net_inflow"),
            "liangbi": r.get("liangbi"),
            "industry_pct": r.get("industry_pct"),
            "fs": {k: r.get(k) for k in F_COLS},
            "total": r.get("total"),
            "model_prob": r.get("model_prob"),
            "pe": r.get("pe_ttm"),
            "catalyst": r.get("catalyst_note", ""),
            "source": r.get("source", ""),
            "warn": r.get("crosscheck_warn", ""),
        })
    out.sort(key=lambda s: _fval(s, "total") or 0, reverse=True)
    for i, s in enumerate(out, 1):
        s["rank"] = i
    return out


def _stocks_estimate(date):
    """预估版：model_top10.csv + crosscheck + review 缓存 → 统一 stock 列表（按 score_total 降序）。"""
    rows = _load_csv(os.path.join(SELECT_DIR, "%s_model_top10.csv" % date))
    cc = _load_json(os.path.join(CACHE_DIR, "crosscheck_%s.json" % date)).get("stocks", {})
    rv = _load_json(os.path.join(CACHE_DIR, "review_%s.json" % date)).get("catalysts", {})
    nm = _name_map()
    out = []
    for r in rows:
        code = r.get("ts_code", "")
        if not code:
            continue
        c = cc.get(code, {})
        ff = (c.get("fund_flow") or {}).get("sources", {}) or {}
        qt = (c.get("quote") or {}).get("sources", {}) or {}
        sc = (c.get("sector") or {}).get("sources", {}) or {}
        main_yuan = None
        for src in ff.values():
            if src.get("main_net_yuan") is not None:
                main_yuan = src["main_net_yuan"]
                break
        last = None
        for src in qt.values():
            if src.get("last") is not None:
                last = src["last"]
                break
        sector_pct = None
        for src in sc.values():
            if src.get("pct") is not None:
                sector_pct = src["pct"]
                break
        cat = rv.get(code, {}) or {}
        out.append({
            "rank": 0,
            "code": code,
            "name": r.get("name") or nm.get(code, ""),
            "price": _fmt_num(last, 2) if last is not None else None,
            "quote_pct": None,
            "main_wan": main_yuan,
            "liangbi": None,
            "industry_pct": sector_pct,
            "fs": {k: r.get("SC_" + k) for k in F_COLS},
            "total": r.get("score_total"),
            "model_prob": r.get("model_prob"),
            "pe": None,
            "catalyst": (cat.get("catalyst_note") or ""),
            "source": "预估(模型分T-1+当日采集)",
            "warn": "",
            "estimate": True,
        })
    out.sort(key=lambda s: _f(s, "total"), reverse=True)
    for i, s in enumerate(out, 1):
        s["rank"] = i
    return out


# ---------------------------------------------------------------------------
# 卡片构建
# ---------------------------------------------------------------------------

def _markdown_table(headers, rows, aligns=None):
    """markdown 表格 → lark_md 可用字符串。"""
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _f_color(v, hi=8, mid=6, hi_c="red", mid_c="orange", lo_c="grey"):
    return hi_c if (v is not None and v >= hi) else (mid_c if (v is not None and v >= mid) else lo_c)


def _top_cards(stocks, n=3):
    """Top N 重点卡（interactive_container，每只一只；含查看行情按钮 + 降级标注）。"""
    elements = []
    for s in stocks[:n]:
        fs = s["fs"]
        f_line = "  ".join(
            "<font color='grey'>%s%s</font>=<font color='%s'>%s</font>" % (
                k, F_LABELS[k], _f_color(_fval(fs, k)), _f(fs, k))
            for k in F_COLS)
        color = _f_color(_fval(s, "total"), hi=70, mid=60, hi_c="red", mid_c="orange", lo_c="blue")
        lines = [
            "<font color='%s'>#%d %s %s</font>　**%s 分**" % (color, s["rank"], s["code"], s["name"] or "", _f(s, "total", 1)),
            "现价 %s 涨跌 %s ｜ 主力 %s ｜ 量比 %s ｜ 板块 %s" % (
                s["price"] or "—", _fmt_pct(s["quote_pct"]) if not s.get("estimate") else "—",
                _fmt_wan(s["main_wan"]) if s["main_wan"] is not None else "—",
                _fmt_num(s["liangbi"], 2) if s["liangbi"] not in (None, "") else "—",
                _fmt_pct(s["industry_pct"]) if s["industry_pct"] not in (None, "") else "—"),
            f_line,
            "<font color='grey'>%s</font>" % _clip(s["catalyst"] or "无催化备注"),
        ]
        # 降级/交叉验证标注上移（单源/不一致 → 该票行内显示）
        if s.get("warn"):
            lines.append("<font color='red'>⚠️ %s</font>" % _clip(s["warn"], 60))
        card = {
            "tag": "interactive_container", "width": "fill", "has_border": True,
            "border_color": "blue-100", "background_style": "blue-50",
            "corner_radius": "8px", "padding": "10px", "vertical_spacing": "2px",
            "margin": "0px 0px 8px 0px",
            "elements": [{"tag": "markdown", "content": ln} for ln in lines],
        }
        # 查看行情按钮（跳东财）
        card["elements"].append({
            "tag": "button", "text": {"tag": "plain_text", "content": "查看行情"},
            "type": "primary_filled", "width": "fill",
            "behaviors": [{"type": "open_url", "default_url": QC.code_to_eastmoney_url(s["code"])}],
        })
        elements.append(card)
    return elements


def _parse_holdings(s):
    """'600028:200|601988:200' → {code: vol}（code 转 6 位）。"""
    d = {}
    for seg in (s or "").split("|"):
        seg = seg.strip()
        if not seg or ":" not in seg:
            continue
        code, vol = seg.split(":", 1)
        code = code.strip().split(".")[0]
        try:
            d[code] = int(float(vol))
        except (TypeError, ValueError):
            d[code] = None
    return d


def _action_zone(action, holdings_map, stocks, is_estimate):
    """📌 操作建议置顶区 + 持仓→目标换仓对比。返回 elements 片段。"""
    els = []
    buy, sell, pos = [], [], []
    for seg in (action or "").split("|"):
        seg = seg.strip()
        if seg.startswith("买入"):
            buy = [x.strip().split(".")[0] for x in seg.split(":", 1)[-1].replace("，", ",").split(",") if x.strip()]
        elif seg.startswith("卖出"):
            sell = [x.strip().split(".")[0] for x in seg.split(":", 1)[-1].replace("，", ",").split(",") if x.strip()]
        elif seg.startswith("仓位"):
            pos.append(seg)
    if not buy and not sell and not pos:
        return els

    lines = []
    if buy:
        lines.append("🟢 买入　<font color='red'>%s</font>" % "/".join(buy))
    if sell:
        lines.append("🔴 卖出　<font color='orange'>%s</font>" % "/".join(sell))
    for p in pos:
        lines.append("📊 %s" % p)
    if not is_estimate:
        # 持仓→目标：现持仓 vs 动作清单
        keep = [c for c in holdings_map if c not in sell]
        target_codes = [s["code"].split(".")[0] for s in stocks[:2]]
        lines.append("🔄 持仓 %d 只 → 目标：卖 %d / 买 %d / 保留 %d" % (
            len(holdings_map), len(sell), len(buy), len(keep)))
    els.append({
        "tag": "interactive_container", "width": "fill", "has_border": True,
        "border_color": "orange-100", "background_style": "orange-50",
        "corner_radius": "8px", "padding": "12px", "vertical_spacing": "4px",
        "margin": "0px 0px 12px 0px",
        "elements": [{"tag": "markdown", "content": "📌 **今日操作建议**\n" + "\n".join(lines)}],
    })
    return els


def _build_card(stocks, date, summary, mode, action="", holdings=""):
    """组装 Card 2.0。"""
    is_estimate = mode == "estimate"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    title = ("G2 盘前预判（预估）" if is_estimate else "G2 盘前复核") + " · %s-%s-%s" % (date[:4], date[4:6], date[6:8])
    template = "blue" if not is_estimate else "indigo"
    tag = "预估版" if is_estimate else "复核版"
    holdings_map = _parse_holdings(holdings)

    # 摘要区
    sum_lines = ["**今日动作/大盘：**"]
    for seg in (summary or "").split("|"):
        seg = seg.strip()
        if seg:
            sum_lines.append("· " + seg)
    if not summary:
        sum_lines.append("· （未提供大盘摘要）")
    if is_estimate:
        sum_lines.append("<font color='orange'>数据为模型预估(T-1特征) + 当日采集，09:45 实时复核后以复核版为准</font>")

    # 双表
    speed_rows, factor_rows = [], []
    for s in stocks[:10]:
        fs = s["fs"]
        speed_rows.append([
            str(s["rank"]), s["name"] or s["code"],
            s["price"] or "—",
            _fmt_pct(s["quote_pct"]) if not is_estimate else "—",
            _fmt_wan(s["main_wan"]) if s["main_wan"] is not None else "—",
            _f(s, "total", 1),
        ])
        factor_rows.append([
            str(s["rank"])] + [_f(fs, k) for k in F_COLS] + [_f(s, "total", 1)],
        )
    speed_tbl = _markdown_table(
        ["排名", "名称", "现价", "涨跌%", "主力(万)", "总分"], speed_rows)
    factor_tbl = _markdown_table(
        ["排名", "F1位置", "F2资金", "F3催化", "F4技术", "F5板块", "F6估值", "总分"], factor_rows)
    factor_legend = "　".join("<font color='grey'>%s=%s</font>" % (k, v) for k, v in F_LABELS.items())

    # 数据源/风险（时间戳明确：数据截至 <mode> 生成时刻）
    warns = [s["warn"] for s in stocks[:10] if s.get("warn")]
    mode_txt = "预估(T-1模型分+当日采集)" if is_estimate else "实时复核(F2/F5当日、F1/F4/F6面板)"
    foot_lines = ["<font color='grey'>数据口径：%s ｜ 数据截至 %s（%s版）</font>" % (mode_txt, ts, tag)]
    if warns:
        foot_lines.append("<font color='red'>⚠️ 交叉验证：%s</font>" % _clip("；".join(warns), 100))
    foot_lines.append("<font color='grey'>⚠️ 研究信号，不构成投资建议</font>")

    elements = []
    # 📌 操作建议置顶
    elements.extend(_action_zone(action, holdings_map, stocks, is_estimate))
    elements.append({"tag": "markdown", "content": "\n".join(sum_lines)})
    # 重点卡（Top3，精简：4-10 只在表内）
    if stocks:
        elements.append({"tag": "markdown", "content": "**Top3 重点**　<font color='grey'>4-10 见下表</font>"})
        elements.extend(_top_cards(stocks, 3))
    # 双表
    if speed_rows:
        elements.append({"tag": "markdown", "content": "**TOP10 速览**"})
        elements.append({"tag": "markdown", "content": speed_tbl})
    if factor_rows:
        elements.append({"tag": "markdown", "content": "**六因子明细**　<font color='grey'>%s</font>" % factor_legend})
        elements.append({"tag": "markdown", "content": factor_tbl})
    # 脚注
    elements.append({"tag": "markdown", "content": "\n".join(foot_lines)})

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "subtitle": {"tag": "plain_text", "content": "%s · Project_16 G2" % ts},
            "template": template,
            "icon": {"tag": "standard_icon", "token": "notification_colorful"},
            "text_tag_list": [
                {"tag": "text_tag", "text": {"tag": "plain_text", "content": tag}, "color": "blue"}],
        },
        "body": {
            "direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "8px",
            "elements": elements,
        },
    }


def main():
    ap = argparse.ArgumentParser(description="G2 盘前 TOP10 打分明细卡片推送")
    ap.add_argument("--date", required=True, help="YYYYMMDD")
    ap.add_argument("--estimate", action="store_true", help="预估版（09:25 预判后）")
    ap.add_argument("--summary", default="", help="大盘/动作摘要，用 | 分隔多行")
    ap.add_argument("--action", default="", help="📌 操作建议：买入:001309/600267|卖出:600028/601988|仓位:正常")
    ap.add_argument("--holdings", default="", help="现持仓：600028:200|601988:200|601398:100（用于换仓对比）")
    ap.add_argument("--no-send", action="store_true", help="只打印卡片 JSON，不发送")
    args = ap.parse_args()

    if args.estimate:
        stocks = _stocks_estimate(args.date)
        mode = "estimate"
    else:
        stocks = _stocks_rich(args.date)
        mode = "rich"
    if not stocks:
        print("[FAIL] 无数据：%s（%s版）" % (args.date, mode))
        sys.exit(1)

    card = _build_card(stocks, args.date, args.summary, mode, args.action, args.holdings)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        print("\n[stocks] %d 只，Top: %s" % (len(stocks), "、".join("%s %s" % (s["code"], s["name"]) for s in stocks[:3])))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → %s" % ("成功" if ok else "失败", mode))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
