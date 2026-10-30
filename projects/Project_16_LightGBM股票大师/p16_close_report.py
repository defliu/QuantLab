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


def _last_artifact_date(dirpath):
    """该目录下最近一个 YYYYMMDD 命名的产物日期；无则返回 None。"""
    import glob
    import re as _re
    if not os.path.isdir(dirpath):
        return None
    ds = []
    for f in glob.glob(os.path.join(dirpath, "*")):
        m = _re.search(r"(20\d{6})", os.path.basename(f))
        if m:
            ds.append(m.group(1))
    return max(ds) if ds else None


def _natural_days_between(d1, d2):
    """d1→d2 之间的**自然日**数（不是交易日数）。

    ★ CC 复核 §4.6：原名 `_trading_days_between` 名实不符——实现只做日期差，
    不查交易日历。20261022→20261030 渲染成「已断 8 天」，实际是 6 个交易日。
    改名 + 文案统一写「自然日」，避免下一个读代码的人误以为它在数交易日。
    真要数交易日就调 is_trade_day，但这里刻意不查：断档提醒宁可按自然日粗算也不静默。
    """
    try:
        a = datetime.strptime(d1, "%Y%m%d")
        b = datetime.strptime(d2, "%Y%m%d")
        return max(0, (b - a).days)
    except Exception:
        return 0


def _reconcile_line(name, path):
    """渲染一条对账产物。返回 (文本, 是否异常)。

    ★ 2026-10-30 审计 §3.1/§3.5（P0）：旧实现把两类**失败**渲染成了正常产出：
      · `data/reconcile/reconcile_<date>.json` 是 `list[2]`，两条元素都是
        `{"connected": false, "error": "连接 miniQMT 失败 rc=-1"}`，
        旧实现走 `isinstance(d, list) → "同步 %d 条"`，读成「同步 2 条」（实际是 2 条连接失败）；
      · 产物缺失时只写「产物缺失」，不区分「今天没跑」与「已经断了一周」
        （ENS 实测已断 6 个交易日，每天一行「产物缺失」看起来像单日偶发）。
    """
    if not os.path.exists(path):
        last = _last_artifact_date(os.path.dirname(path))
        if last:
            gap = _natural_days_between(last, D)
            return ("  %s：✘ 产物缺失（最近产物 %s，已断 %d 个自然日）" % (name, last, gap), True)
        return ("  %s：✘ 产物缺失（目录内无任何历史产物）" % name, True)
    try:
        d = json.load(io.open(path, encoding="utf-8"))
    except Exception as e:
        return ("  %s：✘ 读取失败 %s" % (name, str(e)[:60]), True)

    # 列表形态（reconcile / sync_deals 的多策略返回）：先看有没有连接失败
    if isinstance(d, list):
        if not d:
            return ("  %s：✘ 空产物（0 条记录，未实际执行）" % name, True)
        bad = [x for x in d if isinstance(x, dict) and x.get("connected") is False]
        if bad:
            errs = []
            for x in bad:
                errs.append("%s: %s" % (x.get("strategy") or "?", str(x.get("error") or "")[:60]))
            return ("  %s：✘ 连接失败 %d/%d（%s）" % (name, len(bad), len(d), " | ".join(errs)), True)
        return ("  %s：✔ 同步 %d 条" % (name, len(d)), False)

    if d.get("connected") is False:
        return ("  %s：✘ 连接失败（%s）" % (name, str(d.get("error") or "")[:80]), True)

    issues = d.get("issues") or []
    pending = d.get("pending") or []
    orphans = d.get("orphans") or []
    # ★ 2026-10-30 CC 复核 §4.2：`source_unavailable` 必须**接进渲染**，不能只写不读。
    #   它是产物里明确的「当日快照文件不存在」标记，比 `empty_all` 启发式更准——
    #   后者会把「文件在、positions 真空仓」也误判成「数据源不可读」。
    source_unavail = d.get("source_unavailable")
    empty_all = (not (d.get("fills") or []) and not (d.get("ledger") or {})
                 and not (d.get("account_positions") or {}))
    if source_unavail is True:
        txt = ("  %s：⚠ 数据源不可用（当日快照文件不存在）——本次对账未覆盖成交/持仓"
               "（成交 %s ｜ pending %d ｜ 孤儿 %d ｜ issues %d）"
               % (name, d.get("fills_total", "?"), len(pending), len(orphans), len(issues)))
        return (txt, True)
    if d.get("snapshot_missing") or empty_all:
        txt = ("  %s：⚠ 未做差额判定（成交 %s ｜ pending %d ｜ 孤儿 %d ｜ issues %d）"
               % (name, d.get("fills_total", "?"), len(pending), len(orphans), len(issues)))
        if d.get("snapshot_missing"):
            txt += " ※账户快照缺失"
        if empty_all:
            txt += " ※成交/账本/持仓三者全空"
        return (txt, True)

    return ("  %s：成交 %s ｜ pending %d ｜ 孤儿 %d ｜ issues %d%s" % (
        name, d.get("fills_total", "?"), len(pending), len(orphans), len(issues),
        ("（" + "; ".join(str(x)[:40] for x in issues[:3]) + "）") if issues else ""),
        bool(issues))


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
    if hh > "1510":
        lines.append("【hub】尾盘后已停（正常）")
    elif M._port_open(M.HUB_PORT):
        health = M._hub_health(M.HUB_PORT)
        if health is None:
            lines.append("【hub】%d 在听但 /health 无响应" % M.HUB_PORT)
        else:
            lines.append("【hub】%d " % M.HUB_PORT + " / ".join(
                "%s%s" % (k, "✔" if health[k][0] else "✘") for k in sorted(health.keys())))
    else:
        lines.append("【hub】%d ✘ 未监听" % M.HUB_PORT)
    lines.append("")

    problems = []
    for name, plan_path, log_dir, route_key, model, dry in M.SHELLS:
        plan = M._load_plan(plan_path)
        # 只统计到 15:10 为止：hub 15:10 定时停，之后执行壳的 fetch 失败/窗口外 SKIP
        # 是设计行为，不该算进当日故障口径。
        # ★ `_scan_log` 已按归属态给**每条事件**配上真实时间戳（续行继承首行），
        #   所以这里的 `e[2][11:16]` 就是 HH:MM。旧实现对续行切 `line[:19]` 得到
        #   `[P16SIG][EXEC] FILL`，`[11:16]` 取到 `EC] F`，字符串比较恒 False ⇒ 续行全丢。
        _evts = [e for e in M._scan_log(log_dir, model) if e[2][11:16] <= "15:10"]
        tag, orders, fills, counts, foreign = M._summarize_events(_evts)
        lines.append("【%s】%s计划 %s ｜ %s下单 %d 腿 ｜ 成交 %d 腿 ｜ 窗口外SKIP %d / TRIM %d / 取信号失败 %d%s" % (
            name, ("INIT %s " % tag) if tag else "**无 INIT** ", M._fmt_plan(plan),
            "DRY " if dry else "", len(orders), len(fills),
            counts.get("SKIP", 0), counts.get("TRIM", 0), counts.get("REJECT", 0),
            (" ｜ 他人成交留痕 %d 条" % foreign) if foreign else ""))
        if plan and plan.get("orders"):
            filled = {}
            for code, vol, px, side in fills:
                filled[(side, code)] = filled.get((side, code), 0) + vol
            for leg in plan["orders"]:
                code = leg.get("code")
                act = str(leg.get("action") or "").upper()
                pv = int(leg.get("vol") or 0)
                fv = filled.get((act, code), 0)
                mark = "✔" if fv >= pv and pv > 0 else ("◐" if fv > 0 else "✘")
                lines.append("  %s %s %s 计划 %d / 成交 %d" % (mark, act, code, pv, fv))
                if not dry and fv < pv:
                    rev = filled.get(("SELL" if act == "BUY" else "BUY", code), 0)
                    extra = ("（另有反向成交 %d 股，方向不匹配不计入）" % rev) if rev else ""
                    problems.append("%s %s %s 未完成（计划 %d / 成交 %d）%s"
                                    % (name, act, code, pv, fv, extra))
        elif plan is None and not dry:
            problems.append("%s 计划文件缺失" % name)
        elif not dry:
            problems.append("%s 计划为 0 腿（文件存在但 orders 为空）" % name)
        if not tag:
            problems.append("%s 无 INIT（策略未启动/未加载）" % name)
        lines.append("")

    lines.append("【对账】")
    for name, path in RECONCILE:
        txt, bad = _reconcile_line(name, path)
        lines.append(txt)
        if bad:
            # 剥掉行首缩进与「名字：」前缀，避免 problems 里出现「对账 V13：V13：…」
            detail = txt.strip()
            if detail.startswith(name + "："):
                detail = detail[len(name) + 1:]
            problems.append("对账 %s %s" % (name, detail))
    lines.append("")

    # ★ 运行态 TAG（2026-10-30 立）：盘上产物 ≠ 正在跑的代码时，「已部署」这个结论不成立。
    #   --no-restart 落盘会把新产物写进 python\ 但不加载，必须靠日志 INIT 行判运行态。
    lines.append("【运行态 TAG】")
    try:
        import check_running_tag as TC
        for model, qmt_dir in TC.TARGETS:
            r = TC.check_one(model, qmt_dir, D)
            lines.append("  [%s] %s 盘上 %s / 运行 %s" % (
                r["status"], model, r.get("disk") or "-", r.get("running") or "-"))
            if r["status"] == "STALE":
                problems.append("%s 盘上 %s ≠ 运行 %s（--no-restart 落盘后未加载）" % (
                    model, r.get("disk"), r.get("running")))
            elif r["status"] in ("NOT_RUNNING", "NO_DISK"):
                problems.append("%s %s" % (model, r.get("note") or r["status"]))
    except Exception as e:
        lines.append("  [WARN] 运行态核对失败：%s" % (str(e)[:80],))
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
