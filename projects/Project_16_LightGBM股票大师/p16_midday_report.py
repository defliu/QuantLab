# coding: utf-8
"""P16 午间链路复盘（2026-10-29 重写，替代 DE 版「午休持仓复盘」）。

**为什么重写**：旧版由 LLM 读一堆**已作废**的数据源（文件桥 cfg 账本、`heart_*.json` 心跳、
10-16 的旧持仓快照）拼出报告，结论长期是「ENS 遗留 -17%、V13 心跳 112 万秒」这类**陈旧噪声**，
而且持仓数根本无法实测（xtquant 在 2.1.33 不可用）。

**新口径（只报可验证的事实）**：
  1. hub **单实例 58610** 是否在听，以及 `/health` 里 g2 / g2sbx / v13 三条路由各自 ok
     （2026-10-30 改：旧版查 58710/58711/58712，hub 收成单端口多路由后每天固定三条假警）；
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
    # 名字, 计划文件, 客户端日志目录, hub 路由 key, 模型名, 是否 DRY
    ("G2 主号", os.path.join(HERE, "data", "rebalance_g2", "rebalance_g2_%s.plan.json" % D),
     r"D:\国金QMT交易端模拟", "g2", "STRATEGY_P16_G2_EXEC", False),
    ("G2 沙箱", os.path.join(HERE, "data", "rebalance_g2", "sbx", "rebalance_g2_%s.json" % D),
     r"D:\QMT交易端模拟", "g2sbx", "STRATEGY_P16_G2_EXEC_SBX", True),
    ("V13 主号", os.path.join(HERE, "data", "rebalance_v13", "rebalance_v13_%s.plan.json" % D),
     r"D:\国金QMT交易端模拟", "v13", "STRATEGY_P16_V13_EXEC", False),
]

HUB_PORT = 58610             # 白名单内唯一可用的回环端口

LINE_RE = re.compile(r"\[P16SIG\]\[EXEC\] ([A-Z?\-]+)\s*(.*)$")
ORDER_RE = re.compile(r"(BUY|SELL)\s+([0-9]{6}\.[A-Z]{2})\s+(\d+)@([0-9.]+)")
FILL_RE = re.compile(r"([0-9]{6}\.[A-Z]{2})\s+(\d+)@([0-9.]+)")
# 归属态判据：reqid 行里 `...\python\<模型>.py_NNN`。取模型名做**等值比较**，
# 不用子串匹配（`_G2_EXEC` 会吃掉 `_G2_EXEC_SBX`）。
MODEL_RE = re.compile(r"\\python\\([A-Za-z0-9_]+)\.py_\d+")
TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")


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


def _hub_health(port, timeout=3.0):
    """GET /health → {route_key: (ok, reason)}；不可达返回 None。"""
    try:
        from urllib.request import urlopen
    except ImportError:
        from urllib2 import urlopen
    try:
        resp = urlopen("http://127.0.0.1:%d/health" % int(port), timeout=timeout)
        body = resp.read()
        resp.close()
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    out = {}
    for r in data.get("routes") or []:
        out[str(r.get("key"))] = (bool(r.get("ok")), str(r.get("reason") or ""))
    return out


def _load_plan(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except Exception:
        return None


def _scan_log(client_dir, model):
    """按「归属态」扫日志，返回 [(kind, rest, ts)]。

    **为什么不能用 `模型名.py in line`**（2026-10-30 审计，P0）：QMT 的 FormulaOutput 格式是
    「一条 print 的首行带 `reqid = 0<路径>\\python\\<模型>.py_NNN, output = ` 前缀，多行 print
    的后续行裸写」。按子串过滤只能命中每次打印的**第一行**——实测当日 90 条事件行里 14 条是续行，
    而 G2 的 **ORDER 2/2、FILL 3/3 全是续行**，报告因此把「两条腿全部成交」读成「下单 0 腿、成交 1 腿」。

    **状态机口径**：
      · 命中 `...\\python\\<模型>.py_NNN` 的行 ⇒ 切换当前归属模型，并记录该行时间戳；
      · 不命中归属标记的行 ⇒ 视为**上一条 print 的续行**，继承当前归属模型与时间戳；
      · 归属模型 == 本模型 且 命中 `[P16SIG][EXEC]` ⇒ 计入事件；
      · 归属模型 == 本模型 但**不命中** `[P16SIG][EXEC]`、且上一行是 `[P16SIG][EXEC]` 事件
        ⇒ 产出 `("CONT", 原文, ts)` 续行事件（★ 2026-10-31 CC 复核 §3 修：多行 INIT 的
        `BUILD_TAG=` 就落在这种续行里，不产出 CONT 的话 `_summarize_events` 的 pending_init
        永远等不到，那处修复就是死代码）。

    时间戳随归属一起继承，是为了让盘后报告按 `ts[11:16] <= "15:10"` 过滤时，续行也能拿到
    真实时间（旧实现对续行切 `line[:19]` 得到 `[P16SIG][EXEC] FILL`，`[11:16]` 取到 `EC] F`，
    字符串比较恒为 False ⇒ 修好续行后盘后仍会全丢）。
    """
    p = os.path.join(client_dir, "userdata", "log", "XtClient_FormulaOutput_%s.log" % D)
    if not os.path.exists(p):
        return []
    try:
        txt = open(p, "rb").read().decode("utf-8", "replace")
    except Exception:
        return []
    out = []
    owner = ""
    ts = ""
    prev_was_event = False        # 本模型块内是否已出现过 [P16SIG][EXEC] 事件
                                  # （CC §3.2：它只在切换模型时重置，不随每条 CONT 复位——
                                  #   所以判据实际是「本模型块内出现过事件之后的任意非空裸行」，
                                  #   不是严格的「紧邻上一行是事件」。行为无害：CONT 只喂 pending_init）
    for line in txt.splitlines():
        m = MODEL_RE.search(line)
        if m:
            owner = m.group(1)
            tm = TS_RE.match(line)
            ts = tm.group(1) if tm else ts
        if owner != model:
            prev_was_event = False
            continue
        em = LINE_RE.search(line)
        if em:
            out.append((em.group(1).strip(), em.group(2).strip(), ts))
            prev_was_event = True
            continue
        if prev_was_event and line.strip():
            # 本模型某条事件的续行（裸写行）。只保留非空行，避免把分隔空行当续行。
            out.append(("CONT", line.strip(), ts))
    return out


def _summarize_events(evts):
    """返回 (init_tag, orders, fills, counts, foreign_fills)。

    ★ `FILL?` **绝不并入 fills**（2026-10-30 审计，P0）：执行壳里 `FILL?` 的语义是
    「这条成交不是我的，绝不回写账本」（`strategy_p16_g2_exec.py` 的归属判定）。
    旧实现用 `kind.startswith("FILL")` 把它当自有成交累计，于是
      · V13 面板把 G2 的 2 笔成交算成自己的（纯串账）；
      · G2 沙箱（DRY）把 `FILL? 300@19.100` 当成自己的成交，还因 300>100 判「超额完成 ✔」。
    `FILL?` 单独计数，报告里渲染成「他人成交留痕 N 条」，只作排障线索。

    ★ fills 现在带**方向**（2026-10-30 CC 复核 §3.3）：执行壳的 FILL 行本身不写方向
    （`FILL 603529.SH 300@19.260`），方向只能从同批 ORDER 行推断（`BUY 603529.SH 800@19.23`）。
    旧实现不带方向，导致「计划 SELL 100 / 成交 BUY 800」被判成 ✔ 完成——
    而今天 G2 计划恰好被 19:22 补跑覆盖成了 1 腿 SELL，报告就渲染出一个假 ✔。
    方向推断规则：同一 code 在本模型事件流里最近一条 ORDER 的 action；推断不到记 "?"。
    """
    orders, fills, counts = [], [], {}
    init_tag = ""
    foreign_fills = 0
    side_by_code = {}
    pending_init = False      # 上一事件是「INIT 但未带 TAG」⇒ 等后续 CONT 续行里的 BUILD_TAG
    pending_skipped = 0       # 已跳过多少条未命中 TAG 的 CONT（上限 3，防无限悬置）
    for kind, rest, ts in evts:
        if kind == "CONT":
            # 续行：只服务 pending_init，不计入 counts（它不是独立事件）
            # ★ 2026-10-31 CC 复核 §3.1：`pending_init` 不能在「第一条 CONT 没命中」时清掉——
            #   另一模型的裸续行不带 reqid，owner 不切换，会被当成本模型的 CONT 插进来；
            #   若它恰好夹在 INIT 与 BUILD_TAG= 之间，提前清标志就会丢 TAG（报告误报「无 INIT」）。
            #   现改为：只有**命中 TAG** 才清；未命中就继续等后续 CONT。
            #   容忍 2 条干扰续行，第 3 条仍未命中即放弃（`>= 3`），防 TAG 永久悬置到后面的无关行。
            if pending_init:
                m2 = re.search(r"BUILD_TAG\s*=\s*([0-9A-Za-z._\-]+)", rest)
                if m2:
                    init_tag = m2.group(1)
                    pending_init = False
                else:
                    pending_skipped += 1
                    if pending_skipped >= 3:
                        pending_init = False
                        pending_skipped = 0
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "INIT":
            m = re.search(r"BUILD_TAG=([0-9A-Za-z._\-]+)", rest)
            if m:
                init_tag = m.group(1)
            else:
                # ★ CC 复核 §4.1：INIT 可能打印成多行（首行 `... INIT`、次行 `  BUILD_TAG=xxx`）。
                #   旧实现只在单行里找 `INIT BUILD_TAG=`，多行时 tag 恒空 ⇒ 报告误报「无 INIT」。
                #   TAG 落在下一条 CONT 事件里（由 `_scan_log` 产出）。
                pending_init = True
                pending_skipped = 0     # 新一轮等待重新计数，别继承上一个 INIT 的余数
            continue
        if kind == "ORDER":
            m = ORDER_RE.search(rest)
            if m:
                act, code = m.group(1), m.group(2)
                orders.append((act, code, int(m.group(3)), float(m.group(4))))
                side_by_code[code] = act
        elif kind == "FILL":
            m = FILL_RE.search(rest)
            if m:
                code = m.group(1)
                fills.append((code, int(m.group(2)), float(m.group(3)),
                              side_by_code.get(code, "?")))
        elif kind == "FILL?":
            foreign_fills += 1
    return init_tag, orders, fills, counts, foreign_fills


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
    health = None
    if in_hub_window:
        if _port_open(HUB_PORT):
            health = _hub_health(HUB_PORT)
            if health is None:
                lines.append("【hub】%d 在听但 /health 无响应" % HUB_PORT)
            else:
                lines.append("【hub】%d " % HUB_PORT + " / ".join(
                    "%s%s" % (k, "✔" if health[k][0] else "✘")
                    for k in sorted(health.keys())))
        else:
            lines.append("【hub】%d ✘ 未监听" % HUB_PORT)
    else:
        lines.append("【hub】窗口外（09:15–15:10 之外不判）")
    lines.append("")

    problems = []
    for name, plan_path, log_dir, route_key, model, dry in SHELLS:
        plan = _load_plan(plan_path)
        evts = _scan_log(log_dir, model)
        tag, orders, fills, counts, foreign = _summarize_events(evts)
        lines.append("【%s】%s计划 %s ｜ %s下单 %d 腿 ｜ 成交 %d 腿%s" % (
            name,
            ("INIT %s " % tag) if tag else "**无 INIT** ",
            _fmt_plan(plan),
            "DRY " if dry else "",
            len(orders), len(fills),
            (" ｜ 他人成交留痕 %d 条" % foreign) if foreign else ""))
        # 计划 vs 成交（★ 必须带方向：SELL 计划的成交不能拿 BUY 成交来抵）
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
                    # 反向成交存在时要说清，避免「成交了但方向反了」被当成单纯未完成
                    rev = filled.get(("SELL" if act == "BUY" else "BUY", code), 0)
                    extra = ("（另有反向成交 %d 股，方向不匹配不计入）" % rev) if rev else ""
                    problems.append("%s %s %s 未完成（计划 %d / 成交 %d）%s"
                                    % (name, act, code, pv, fv, extra))
        elif plan is None and not dry:
            problems.append("%s 计划文件缺失：%s" % (name, os.path.basename(plan_path)))
        elif not dry:
            # 文件在、但 orders 为空 = 当日算出 0 腿（与「文件没生成」是两件事，别混报）
            problems.append("%s 计划为 0 腿（文件存在但 orders 为空）" % name)
        if not tag:
            problems.append("%s 无 INIT（策略未启动/未加载）" % name)
        if in_hub_window:
            if health is None:
                problems.append("%s hub %d 未监听/不可达" % (name, HUB_PORT))
            elif route_key not in health:
                problems.append("%s hub 缺路由 %s" % (name, route_key))
            elif not health[route_key][0]:
                problems.append("%s hub 路由 %s 无信号（%s）" % (
                    name, route_key, health[route_key][1] or "原因未给"))
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
