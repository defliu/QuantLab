# coding: utf-8
"""P16 午间链路复盘（2026-10-29 重写，替代 DE 版「午休持仓复盘」）。

**为什么重写**：旧版由 LLM 读一堆**已作废**的数据源（文件桥 cfg 账本、`heart_*.json` 心跳、
10-16 的旧持仓快照）拼出报告，结论长期是「ENS 遗留 -17%、V13 心跳 112 万秒」这类**陈旧噪声**，
而且持仓数根本无法实测（xtquant 在 2.1.33 不可用）。

**新口径（只报可验证的事实）**：
  1. 三个 hub 端口是否在听（58710/58711/58712）；
  2. 当天**计划腿**（G2 主号 / G2 沙箱 / V13 主号）与执行壳日志里的 **ORDER / FILL** 对齐，
     直接给出「计划 N 腿 → 下单 M 腿 → 成交 K 腿」以及逐腿差异；
  3. **不推测持仓**：外部没有可靠持仓源，报告明写「以客户端为准」。

产物：data/schedules/p16_midday_<date>.md 并推飞书（LARK_SRC_TAG=[QD]）。
退出码：0 正常 / 2 推送失败。
"""
from __future__ import print_function

import io
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import is_trade_day as ITD  # noqa: E402

D = time.strftime("%Y%m%d")
SCHED = os.path.join(HERE, "data", "schedules")

SHELLS = [
    # 名字, 计划文件, 客户端日志目录, hub 端口, 是否 DRY
    ("G2 主号", os.path.join(HERE, "data", "rebalance_g2", "rebalance_g2_%s.plan.json" % D),
     r"D:\国金QMT交易端模拟", 58710, False),
    ("G2 沙箱", os.path.join(HERE, "data", "rebalance_g2", "sbx", "rebalance_g2_%s.json" % D),
     r"D:\QMT交易端模拟", 58711, True),
    ("V13 主号", os.path.join(HERE, "data", "rebalance_v13", "rebalance_v13_%s.plan.json" % D),
     r"D:\国金QMT交易端模拟", 58712, True),
]

LINE_RE = re.compile(r"\[P16SIG\]\[EXEC\] ([A-Z?\-]+)\s*(.*)$")
ORDER_RE = re.compile(r"(BUY|SELL)\s+([0-9]{6}\.[A-Z]{2})\s+(\d+)@([0-9.]+)")
FILL_RE = re.compile(r"([0-9]{6}\.[A-Z]{2})\s+(\d+)@([0-9.]+)")


def _port_open(port):
    s = socket.socket()
    s.settimeout(0.8)
    try:
        s.connect(("127.0.0.1", int(port)))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _load_plan(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return None


def _scan_log(client_dir):
    p = os.path.join(client_dir, "userdata", "log", "XtClient_FormulaOutput_%s.log" % D)
    if not os.path.exists(p):
        return []
    try:
        txt = open(p, "rb").read().decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    for line in txt.splitlines():
        m = LINE_RE.search(line)
        if m:
            out.append((m.group(1).strip(), m.group(2).strip(), line[:19]))
    return out


def _summarize_events(evts):
    orders, fills, counts = [], [], {}
    init_tag = ""
    for kind, rest, ts in evts:
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "INIT":
            m = re.search(r"BUILD_TAG=([0-9A-Za-z._\-]+)", rest)
            if m:
                init_tag = m.group(1)
        elif kind == "ORDER":
            m = ORDER_RE.search(rest)
            if m:
                orders.append((m.group(1), m.group(2), int(m.group(3)), float(m.group(4))))
        elif kind.startswith("FILL"):
            m = FILL_RE.search(rest)
            if m:
                fills.append((m.group(1), int(m.group(2)), float(m.group(3))))
    return init_tag, orders, fills, counts


def _fmt_plan(plan):
    if not plan:
        return "计划文件缺失"
    legs = plan.get("orders") or []
    return "%d 腿" % len(legs)


def _hhmm():
    return time.strftime("%H%M")


def main():
    no_push = "--no-push" in sys.argv
    in_hub_window = "0915" <= _hhmm() <= "1510"
    try:
        _cal = ITD.load_calendar()
        r = ITD.is_trade_day(datetime.now().date(), _cal)
        is_td = bool(r.get("is_trade_day")) if isinstance(r, dict) else bool(r)
    except Exception:
        is_td = True
    if not is_td:
        print("[MIDDAY] 非交易日，跳过")
        return 0

    lines = ["P16 午间链路复盘 %s %s" % (D, time.strftime("%H:%M")), ""]
    if "0915" <= _hhmm() <= "1510":
        lines.append("【hub】" + " / ".join(
            "%d%s" % (p, "✔" if _port_open(p) else "✘") for p in (58710, 58711, 58712)))
    else:
        lines.append("【hub】窗口外（09:15–15:10 之外不判）")
    lines.append("")

    problems = []
    for name, plan_path, log_dir, port, dry in SHELLS:
        plan = _load_plan(plan_path)
        evts = _scan_log(log_dir)
        tag, orders, fills, counts = _summarize_events(evts)
        lines.append("【%s】%s计划 %s ｜ %s下单 %d 腿 ｜ 成交 %d 腿" % (
            name,
            ("INIT %s " % tag) if tag else "**无 INIT** ",
            _fmt_plan(plan),
            "DRY " if dry else "",
            len(orders), len(fills)))
        # 计划 vs 成交
        if plan and plan.get("orders"):
            filled = {}
            for code, vol, px in fills:
                filled[code] = filled.get(code, 0) + vol
            for leg in plan["orders"]:
                code = leg.get("code")
                pv = int(leg.get("vol") or 0)
                fv = filled.get(code, 0)
                mark = "✔" if fv >= pv and pv > 0 else ("◐" if fv > 0 else "✘")
                lines.append("  %s %s 计划 %d / 成交 %d" % (mark, code, pv, fv))
                if not dry and fv < pv:
                    problems.append("%s %s 未完成（计划 %d / 成交 %d）" % (name, code, pv, fv))
        elif not dry:
            problems.append("%s 计划文件缺失：%s" % (name, os.path.basename(plan_path)))
        if not tag:
            problems.append("%s 无 INIT（策略未启动/未加载）" % name)
        if in_hub_window and not _port_open(port):
            problems.append("%s hub %d 未监听" % (name, port))
        lines.append("")

    lines.append("【持仓】外部无可用持仓源（xtquant 在 2.1.33 不可用）；仓位请以 QMT 客户端为准，本报告不推测。")
    lines.append("【口径】计划来自 09:36（V13）/09:40（G2）纯脚本任务；成交来自客户端 FormulaOutput 的 [P16SIG][EXEC] 行。")
    if problems:
        lines.append("")
        lines.append("【需关注】")
        for p in problems:
            lines.append("- " + p)

    body = "\n".join(lines)
    if not os.path.isdir(SCHED):
        os.makedirs(SCHED)
    out = os.path.join(SCHED, "p16_midday_%s.md" % D)
    io.open(out, "w", encoding="utf-8").write(body + "\n")
    print(body)
    print("\n[MIDDAY] 正文已落盘 %s" % out)
    # 计划任务无控制台：落一行摘要便于事后核对
    try:
        with io.open(os.path.join(SCHED, "p16_midday.log"), "a", encoding="utf-8") as f:
            f.write(u"[%s] problems=%d | %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), len(problems), body.splitlines()[0]))
    except Exception:
        pass

    if no_push:
        print("[MIDDAY] --no-push：跳过推送")
        return 0
    try:
        env = dict(os.environ)
        env["LARK_SRC_TAG"] = "[QD]"
        rc = subprocess.call([sys.executable, os.path.join(HERE, "push_alert_card.py"),
                              "--title", "P16 午间链路复盘", "--body-file", out,
                              "--level", "warn" if problems else "info"],
                             cwd=HERE, env=env, timeout=90)
        if rc != 0:
            print("[MIDDAY] 推送返回 %d" % rc)
            return 2
    except Exception as e:
        print("[MIDDAY] 推送异常 %s" % (str(e)[:120],))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
