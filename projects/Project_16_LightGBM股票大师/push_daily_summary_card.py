# coding: utf-8
"""盘后总览卡片推送：对账后推一张总览，覆盖【所有已上线策略】。

注册表驱动（2026-10-14 改造）：新增上线策略 = 在 `_live_strategies()` 追加一项，
卡片自动纳入，不再写死 V1.3 + G2。每策略一行：持仓 / 成交（买·卖）/ 浮动 / 累计（红涨绿跌配色）。

数据源（统一复用 push_strategy_metrics_card 的 loader 与成交源，单一事实源，不重复读成交文件）：
  - V1.3   : data/strategy_capital.json + p16_v13_risk/*（成交源 fills_v13_<date>.json）
  - V2     : v2_holdings_state.json + strategy_log_v2.txt + reconcile_v2_<date>.json
             （成交源 = reconcile 的 orders_v2，方向取自 remark 的「V2买入/V2卖出」）
  - G2     : g2_bridge/g2_strategy_capital.json + g2_bridge/state/fills_<date>.json
  - 融合   : p16_ensemble_bridge/ens_strategy_capital.json + .../state/fills_<date>.json
  - 对账摘要：--summary / --summary-file 传入（reconcile 脚本输出，向后兼容）

用法：
  python push_daily_summary_card.py --date 20261014 --summary "G2对账:无差额|无孤儿"
  python push_daily_summary_card.py --date 20261014 --no-send
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

PROJECT = os.path.dirname(os.path.abspath(__file__))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import qmt_card as QC  # noqa: E402
import push_strategy_metrics_card as PM  # noqa: E402  复用 loader / 格式化 / 配色

# ---------------- 已上线策略注册表 ----------------

def _live_strategies(date):
    """【注册表】所有已上线策略。新增上线策略 = 在此追加一项，卡片自动纳入。
    每项标准化：{short, acct, capital, hold, float, buy, sell, fills, heartbeat}。
    成交数据（buy/sell/fills）由 PM loader 统一提供（单一事实源），本卡不重复读成交文件。
    沙箱外采集（T-20261016-001）：与经营指标卡同源，一次现采共用。"""
    m = PM._sandbox_metrics(date)
    specs = [
        (PM._load_v13(date, m=m), "67014907"),
        (PM._load_v2(date, mets=m), "67014907"),
        (PM._load_bridge(PM.G2_CAP, PM.G2_HEART_DIR, "heart_", PM.G2_RECON_DIR,
                         "reconcile_g2_", date, "G2 大QMT文件桥", "QMT桥 · 70180771",
                         fills_path=PM.G2_FILLS, short="G2", sb_tag="G2", m=m), "70180771"),
        (PM._load_bridge(PM.ENS_CAP, PM.ENS_HEART_DIR, "heart_", PM.ENS_RECON_DIR,
                         "reconcile_g2_ens_", date, "融合大QMT文件桥", "QMT桥 · 70180771",
                         fills_path=PM.ENS_FILLS, short="融合", sb_tag="ENS", m=m), "70180771"),
    ]
    return [{
        "short": s.get("short"), "acct": acct,
        "capital": s.get("capital"), "hold": s.get("hold"), "float": s.get("float"),
        "buy": s.get("buy"), "sell": s.get("sell"), "fills": s.get("fills"),
        "heartbeat": s.get("heartbeat"),
    } for s, acct in specs]


# ---------------- 卡片构建 ----------------

def _strat_line(s):
    """单策略一行：持仓 / 成交 / 浮动 / 累计（红涨绿跌）。"""
    cum = PM._cum_pct(s.get("capital"))
    color = PM._color(cum)
    cum_txt = ("%+.2f%%" % cum) if cum is not None else "—"
    if s.get("buy") is None:
        trade_txt = "成交 —"
    else:
        trade_txt = "成交 %d笔（买%d/卖%d）" % (
            int(s["buy"]) + int(s["sell"]), int(s["buy"]), int(s["sell"]))
    return ("**%s（%s）**　持仓 %d只 · %s · 浮动 %s · 累计 <font color='%s'>%s</font>"
            % (s["short"], s["acct"], int(s.get("hold") or 0), trade_txt,
               PM._signed_with_pct(s.get("float")), color, cum_txt))


def _fills_table(strategies):
    """汇总当日所有成交（跨策略）到一张表。无成交返回 None。"""
    rows = []
    for s in strategies:
        for f in s.get("fills") or []:
            act = "买" if f.get("action") == "BUY" else (
                "卖" if f.get("action") == "SELL" else (f.get("action") or "—"))
            rows.append("| %s | %s | %s | %s | %s | %s |" % (
                s["short"], act, f.get("code", ""), PM._fmt(f.get("vol"), 0),
                PM._fmt(f.get("price")), f.get("status", "")))
    if not rows:
        return None
    head = "| 策略 | 方向 | 代码 | 数量 | 价格 | 状态 |"
    sep = "|---|---|---|---|---|---|"
    return "\n".join([head, sep] + rows)


def _build_card(strategies, date, summary):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    dep = [s for s in strategies if s.get("capital") is not None]
    tot_cap = sum(float(s["capital"]) for s in dep)
    tot_base = PM.CAP_BASE * len(dep)
    tot_cum = ((tot_cap / tot_base - 1) * 100) if tot_base else None
    tot_color = PM._color(tot_cum)
    tot_pct = ("%+.2f%%" % tot_cum) if tot_cum is not None else "—"

    lines = ["已上线 **%d** 个策略　合计资金池 **%s**　综合累计 <font color='%s'>%s</font>"
             % (len(dep), PM._money(tot_cap), tot_color, tot_pct)]
    for s in dep:
        lines.append(_strat_line(s))
    for seg in (summary or "").split("|"):
        seg = seg.strip()
        if seg:
            lines.append("· %s" % seg)

    elements = [{"tag": "markdown", "content": "\n".join(lines)}]
    tbl = _fills_table(strategies)
    if tbl:
        elements.append({"tag": "markdown", "content": "**今日成交明细**"})
        elements.append({"tag": "markdown", "content": tbl})
    elements.append({
        "tag": "markdown", "text_size": "notation", "text_align": "center",
        "content": "<font color='grey'>未部署：%s\n盘后总览 · 数据截至 %s\n⚠️ 研究信号，不构成投资建议</font>"
                   % (" · ".join(PM.NOT_DEPLOYED), ts),
    })

    return {
        "schema": "2.0",
        "config": {"width_mode": "default", "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "盘后总览 · %s-%s-%s" % (date[:4], date[4:6], date[6:8])},
            "subtitle": {"tag": "plain_text", "content": "%s · %s" % (
                ts, " + ".join(s["short"] for s in dep))},
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
    ap.add_argument("--summary", default="", help="对账摘要，用 | 分隔")
    ap.add_argument("--summary-file", default=None,
                    help="从 UTF-8 文件读对账摘要（长文本安全通道）")
    ap.add_argument("--no-send", action="store_true")
    args = ap.parse_args()
    summary, serr = _read_summary(args)
    if serr:
        print("[FAIL] %s" % serr)
        sys.exit(3)
    strategies = _live_strategies(args.date)
    for s in strategies:
        cum = PM._cum_pct(s.get("capital"))
        print("[DATA] %-10s 资金池=%s 累计=%s 持仓=%s 成交=%s"
              % (s["short"], PM._money(s.get("capital")),
                 ("%+.2f%%" % cum) if cum is not None else "—", s.get("hold"),
                 "—" if s.get("buy") is None else "%d笔(买%d/卖%d)" % (
                     int(s["buy"]) + int(s["sell"]), int(s["buy"]), int(s["sell"]))))
    card = _build_card(strategies, args.date, summary)
    if args.no_send:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return
    ok = QC.send_lark_card(card)
    print("[SEND] %s → 盘后总览" % ("成功" if ok else "失败"))
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
