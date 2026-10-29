# coding: utf-8
"""P16 盘后复盘（2026-10-29 重写，替代 DE 版「盘后持仓复盘」）。

与午间版同一口径（见 p16_midday_report.py 的说明）：**只报可验证事实**，
不再由 LLM 读文件桥 cfg 账本 / `heart_*.json` / 旧快照拼结论。

盘后额外多一节「对账」，读当天已落盘的 reconcile 产物（16:40 由
`Quant_P16_DE_Reconcile_Review` 前一槽 / `reconcile_g2.py` 产出）：
fills 数、pending、orphans、issues 数量；没有产物就明写「对账产物缺失」。

产物：data/schedules/p16_close_<date>.md + 飞书卡（LARK_SRC_TAG=[QD]）。
退出码：0 正常 / 2 推送失败。
"""
from __future__ import print_function

import io
import json
import os
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
import is_trade_day as ITD            # noqa: E402
import p16_midday_report as M         # noqa: E402  复用端口/计划/日志解析

D = time.strftime("%Y%m%d")
SCHED = os.path.join(HERE, "data", "schedules")

RECONCILE = [
    ("G2", os.path.join(HERE, "data", "reconcile_g2", "reconcile_g2_%s.json" % D)),
    ("ENS", os.path.join(HERE, "data", "reconcile_g2_ens", "reconcile_g2_ens_%s.json" % D)),
    ("V13", os.path.join(HERE, "data", "reconcile", "reconcile_%s.json" % D)),
    ("同步成交", os.path.join(HERE, "data", "reconcile", "sync_deals_%s.json" % D)),
]


def _reconcile_line(name, path):
    if not os.path.exists(path):
        return "  %s：产物缺失（%s）" % (name, os.path.basename(path))
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        return "  %s：读取失败 %s" % (name, str(e)[:60])
    if isinstance(d, list):
        return "  %s：同步 %d 条" % (name, len(d))
    issues = d.get("issues") or []
    pending = d.get("pending") or []
    orphans = d.get("orphans") or []
    return "  %s：成交 %s ｜ pending %d ｜ 孤儿 %d ｜ issues %d%s" % (
        name, d.get("fills_total", "?"), len(pending), len(orphans), len(issues),
        ("（" + "; ".join(str(x)[:40] for x in issues[:3]) + "）") if issues else "")


def main():
    no_push = "--no-push" in sys.argv
    try:
        _cal = ITD.load_calendar()
        r = ITD.is_trade_day(datetime.now().date(), _cal)
        is_td = bool(r.get("is_trade_day")) if isinstance(r, dict) else bool(r)
    except Exception:
        is_td = True
    if not is_td:
        print("[CLOSE] 非交易日，跳过")
        return 0

    hh = time.strftime("%H%M")
    lines = ["P16 盘后复盘 %s %s" % (D, time.strftime("%H:%M")), ""]
    lines.append("【hub】" + ("尾盘后已停（正常）" if hh > "1510" else " / ".join(
        "%d%s" % (p, "✔" if M._port_open(p) else "✘") for p in (58710, 58711, 58712))))
    lines.append("")

    problems = []
    for name, plan_path, log_dir, port, dry in M.SHELLS:
        plan = M._load_plan(plan_path)
        # 只统计到 15:10 为止：hub 15:10 定时停，之后执行壳的 fetch 失败/窗口外 SKIP
        # 是设计行为，不该算进当日故障口径
        _evts = [e for e in M._scan_log(log_dir) if e[2][11:16] <= "15:10"]
        tag, orders, fills, counts = M._summarize_events(_evts)
        lines.append("【%s】%s计划 %s ｜ %s下单 %d 腿 ｜ 成交 %d 腿 ｜ 窗口外SKIP %d / TRIM %d / 取信号失败 %d" % (
            name, ("INIT %s " % tag) if tag else "**无 INIT** ", M._fmt_plan(plan),
            "DRY " if dry else "", len(orders), len(fills),
            counts.get("SKIP", 0), counts.get("TRIM", 0), counts.get("REJECT", 0)))
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
            problems.append("%s 计划文件缺失" % name)
        if not tag:
            problems.append("%s 无 INIT（策略未启动/未加载）" % name)
        lines.append("")

    lines.append("【对账】")
    for name, path in RECONCILE:
        lines.append(_reconcile_line(name, path))
    lines.append("")

    lines.append("【持仓】外部无可用持仓源（xtquant 在 2.1.33 不可用）；仓位与遗留仓请以 QMT 客户端为准，本报告不推测。")
    lines.append("【口径】计划=09:36/09:40 纯脚本任务；成交=客户端 FormulaOutput 的 [P16SIG][EXEC] 行；对账=当天 reconcile 产物。")
    if problems:
        lines.append("")
        lines.append("【需关注】")
        for p in problems:
            lines.append("- " + p)

    body = "\n".join(lines)
    if not os.path.isdir(SCHED):
        os.makedirs(SCHED)
    out = os.path.join(SCHED, "p16_close_%s.md" % D)
    io.open(out, "w", encoding="utf-8").write(body + "\n")
    print(body)
    print("\n[CLOSE] 正文已落盘 %s" % out)
    try:
        with io.open(os.path.join(SCHED, "p16_close.log"), "a", encoding="utf-8") as f:
            f.write(u"[%s] problems=%d | %s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), len(problems), body.splitlines()[0]))
    except Exception:
        pass

    if no_push:
        print("[CLOSE] --no-push：跳过推送")
        return 0
    try:
        env = dict(os.environ)
        env["LARK_SRC_TAG"] = "[QD]"
        rc = subprocess.call([sys.executable, os.path.join(HERE, "push_alert_card.py"),
                              "--title", "P16 盘后复盘", "--body-file", out,
                              "--level", "warn" if problems else "info"],
                             cwd=HERE, env=env, timeout=90)
        if rc != 0:
            print("[CLOSE] 推送返回 %d" % rc)
            return 2
    except Exception as e:
        print("[CLOSE] 推送异常 %s" % (str(e)[:120],))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
