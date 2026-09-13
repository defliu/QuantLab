# -*- coding: utf-8 -*-
"""提取 DE 盘后持仓复盘报告的核心持仓数据，输出紧凑推送文本。

供 de_report_daily.ps1 调用：python extract_de_report_push.py <报告.md>
输出 UTF-8 到 stdout；无持仓行时输出 FAIL_NO_ROWS 并以 exit 1 退出。
数据口径与报告一致：止损线=成本x0.93、止盈线=成本x1.15（P16 系统一口径）。
"""
import io
import re
import sys


def clean(cell):
    return cell.replace("**", "").strip()


def main():
    if len(sys.argv) < 2:
        print("USAGE: extract_de_report_push.py <report.md>")
        sys.exit(1)
    path = sys.argv[1]
    text = io.open(path, encoding="utf-8").read()
    lines = text.splitlines()

    rows = []
    in_detail = False
    for ln in lines:
        if ln.startswith("## 二"):
            in_detail = True
            continue
        if in_detail and ln.startswith("## "):
            break
        if not in_detail or not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 15 or not re.match(r"^\d{6}\.[A-Z]{2}$", cells[0]):
            continue
        code = cells[0]
        name = clean(cells[1])
        strat = clean(cells[2])
        try:
            shares = int(cells[3].replace(",", ""))
            cost = float(cells[4].replace(",", ""))
            px = float(cells[5].replace(",", ""))
        except ValueError:
            continue
        chg = ""
        m = re.search(r"([+-]?\d+\.\d+)%", cells[6])
        if m:
            chg = m.group(1)
        rows.append((code, name, strat, shares, cost, px, chg))

    if not rows:
        print("FAIL_NO_ROWS")
        sys.exit(1)

    date = ""
    m = re.search(r"(\d{8})", path)
    if m:
        d = m.group(1)
        date = "%s-%s-%s" % (d[:4], d[4:6], d[6:8])

    total_pnl = sum((px - cost) * shares for _, _, _, shares, cost, px, _ in rows)

    realized = ""
    m2 = re.search(r"当日已实现盈亏合计：\*\*(-?[\d,]+\.\d+)\s*元\*\*", text)
    if m2:
        realized = m2.group(1)

    head = "📊 P16 持仓复盘 · %s\n💼 总持仓 %d 只 ｜ 总盈亏 %s 元" % (date, len(rows), fmt_amt(total_pnl))
    if realized:
        head += " ｜ 当日已实现 %s 元" % realized

    strat_emoji = {"V1.3": "🟦", "G2": "🟩", "融合": "🟧"}

    cap = {}
    in_cap = False
    for ln in lines:
        if ln.startswith("## 三"):
            in_cap = True
            continue
        if in_cap and ln.startswith("## "):
            break
        if in_cap and ln.startswith("|"):
            cc = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cc) >= 2 and cc[0] in ("V1.3", "V13", "G2", "融合ENS", "融合"):
                m3 = re.search(r"([\d,]+\.\d+)\s*元", cc[1])
                note = clean(cc[3]) if len(cc) > 3 else ""
                if m3:
                    cap[cc[0]] = (float(m3.group(1).replace(",", "")), note)

    out = [head]
    order = []
    groups = {}
    for row in rows:
        strat = row[2]
        if strat not in groups:
            groups[strat] = []
            order.append(strat)
        groups[strat].append(row)

    for strat in order:
        grp = groups[strat]
        gpnl = sum((px - cost) * shares for _, _, _, shares, cost, px, _ in grp)
        emoji = "⬜"
        for k, v in strat_emoji.items():
            if k in strat:
                emoji = v
                break
        gname = strat.replace("ENS", "").strip()
        out.append("")
        out.append("%s %s ｜ %d只 ｜ 盈亏 %s 元" % (emoji, gname, len(grp), fmt_amt(gpnl)))
        if strat in cap:
            v, note = cap[strat]
            d = v - 100000.0
            dpct = d / 100000.0 * 100.0
            cap_line = "  💰 资金池 %s 元 ｜ 较初始10万 %s(%s%%)" % ("{:,.2f}".format(v), fmt_amt(d), fmt_pct(dpct))
            if note:
                short = note
                if len(short) > 30:
                    short = short[:30] + "..."
                cap_line += " ｜ 注：%s" % short
            out.append(cap_line)
        for code, name, strat2, shares, cost, px, chg in grp:
            pnl_amt = (px - cost) * shares
            pnl_pct = (px / cost - 1.0) * 100.0
            stop = cost * 0.93
            tp = cost * 1.15
            stop_buf = (px - stop) / px * 100.0
            tp_space = (tp - px) / px * 100.0
            rr = tp_space / stop_buf if stop_buf > 1e-9 else 99.99
            if pnl_amt < 0:
                rr = -rr
            if stop_buf < 3.0:
                flag = "🔴"
            elif stop_buf < 6.0:
                flag = "🟡"
            else:
                flag = "✅"
            pnl_word = "浮盈" if pnl_amt >= 0 else "浮亏"
            line = "  · %s %s 现价%.2f" % (code, name, px)
            if chg:
                line += " 当日%s%%" % chg
            line += " ｜ %s%s元(%s%%)" % (pnl_word, fmt_amt(pnl_amt), fmt_pct(pnl_pct))
            line += " ｜ 止损余%.2f%%%s ｜ 盈亏比%.2f" % (stop_buf, flag, rr)
            out.append(line)

    print("\n".join(out))


def fmt_amt(n):
    return "{:+,.2f}".format(n)


def fmt_pct(n):
    return "{:+,.2f}".format(n)


if __name__ == "__main__":
    main()
