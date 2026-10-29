# coding: utf-8
"""P16 链路健康巡检（原「桥心跳超时告警」，2026-10-29 重写）。

**为什么重写**：旧版盯 `D:/QMT_POOL/*/state/heart_*.json`——文件桥 2026-10-15 起作废、
心跳文件再没人写（ENS/V13 的停在 10-16），于是每次巡检必报「心跳超时 112 万秒」这种
**结构性假警**（2026-10-29 15:00 实测）。现在的下单链路是「外部计划 → 回环 hub → 内置执行壳
passorder」，心跳该看的东西全变了。

**现在检查什么（交易日 09:30–15:10 窗口内）**：
  1. 回环 hub 是否在听（58710 G2主号 / 58711 G2沙箱 / 58712 V13主号）；
  2. 各执行壳今天是否有 `[P16SIG][EXEC] INIT BUILD_TAG=`（= 策略加载并运行）；
  3. 09:45 之后是否已生成当天计划文件（hub 的输入）；
  4. 执行窗口内是否出现过 `[P16SIG][EXEC] PLAN`（= 真拿到了信号，不是空转）。

**ENS 不再监控**：它的 xtquant 外部腿在 2.1.33 上已不可用（`XtMiniQmt.exe` 不再自带），
本条只留一行 SKIP 说明，不当故障报。

退出码：0 正常 / 2 发现异常（已告警）。
"""
from __future__ import print_function

import json
import os
import socket
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

MAX_PLAN_WAIT_SEC = 900      # 09:45 之后 15 分钟内计划应就绪
PROJ = HERE

# 逻辑名 -> (客户端目录, 模型名, hub 端口, 计划目录, 计划文件名模板)
SHELLS = {
    "G2-main": (r"D:\国金QMT交易端模拟", "STRATEGY_P16_G2_EXEC", 58710,
                os.path.join(PROJ, "data", "rebalance_g2"), "rebalance_g2_%s.plan.json"),
    "G2-sbx": (r"D:\QMT交易端模拟", "STRATEGY_P16_G2_EXEC_SBX", 58711,
               os.path.join(PROJ, "data", "rebalance_g2", "sbx"), "rebalance_g2_%s.json"),
    "V13-main": (r"D:\国金QMT交易端模拟", "STRATEGY_P16_V13_EXEC", 58712,
                 os.path.join(PROJ, "data", "rebalance_v13"), "rebalance_v13_%s.plan.json"),
}


def _today():
    return time.strftime("%Y%m%d")


def _hhmm():
    return time.strftime("%H%M")


def _port_open(port, timeout=1.0):
    s = socket.socket()
    s.settimeout(timeout)
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


def _read_log(client_dir, date):
    p = os.path.join(client_dir, "userdata", "log", "XtClient_FormulaOutput_%s.log" % date)
    if not os.path.exists(p):
        return "", p
    try:
        return open(p, "rb").read().decode("utf-8", "replace"), p
    except Exception:
        return "", p


def check_shell(name, client_dir, model, port, plan_dir, plan_tpl):
    """返回 (level, 说明)；level: OK / ALERT / WARN。"""
    date = _today()
    txt, logp = _read_log(client_dir, date)
    inited = ("[P16SIG][EXEC] INIT BUILD_TAG=" in txt)
    planned = ("[P16SIG][EXEC] PLAN" in txt)
    running_init = ("[P16SIG][EXEC] MODE=" in txt) or inited

    problems = []
    if not _port_open(port):
        problems.append("hub %d 未监听" % port)
    if not inited:
        problems.append("策略 %s 今天没有 INIT（未启动/未加载）" % model)
    # 计划文件：09:45 之后必须有（G2 09:40 / V13 09:36 出计划）
    plan_path = os.path.join(plan_dir, plan_tpl % date)
    if _hhmm() >= "0945" and not os.path.exists(plan_path):
        try:
            late = int(_hhmm()[:2]) * 3600 + int(_hhmm()[2:]) * 60 - (9 * 3600 + 45 * 60)
        except Exception:
            late = 0
        if late > MAX_PLAN_WAIT_SEC:
            problems.append("计划未生成 %s（09:40/09:36 任务失败？）" % os.path.basename(plan_path))
    # 窗口内应见过 PLAN 行（09:50 起；只在窗口内提示，不当硬故障）
    if "0950" <= _hhmm() <= "1000" and inited and not planned:
        problems.append("执行窗口内未见 PLAN 行（hub 无信号或校验被拒）")

    if problems:
        return "ALERT", " | ".join(problems)
    if inited and planned:
        return "OK", "已加载并拿到信号"
    if running_init:
        return "OK", "已加载（窗口外/尚未到窗口）"
    return "WARN", "未加载"


def main():
    try:
        _cal = ITD.load_calendar()
        _res = ITD.is_trade_day(datetime.now().date(), _cal)
        _is_td = bool(_res.get("is_trade_day")) if isinstance(_res, dict) else bool(_res)
    except Exception as e:
        _is_td = True          # 判不出来就按交易日跑（宁可多查一次，不静默跳过）
        print("[CHAIN] WARN 交易日判定失败(%s)，按交易日继续" % (str(e)[:80],))
    if not _is_td:
        print("[CHAIN] 非交易日，跳过")
        return 0
    hh = _hhmm()
    if hh < "0930" or hh > "1510":
        print("[CHAIN] 窗口外（%s），跳过" % hh)
        return 0

    alerts = []
    print("[CHAIN] %s 巡检开始" % time.strftime("%Y-%m-%d %H:%M:%S"))
    for name, args in SHELLS.items():
        level, msg = check_shell(name, *args)
        print("  [%s] %-9s %s" % (level, name, msg))
        if level == "ALERT":
            alerts.append("%s: %s" % (name, msg))
    print("  [SKIP] ENS      已下线（xtquant 在 2.1.33 不可用，无外部下单腿）")

    if not alerts:
        print("[CHAIN] 全部正常")
        return 0

    body = ("链路巡检发现异常（%s）：\n" % time.strftime("%H:%M")) + "\n".join("- " + a for a in alerts) + \
           "\n\n排查方向：hub 未听 → `scripts/qmt_signal_hub.ps1 -Mode Start`；" \
           "无 INIT → 客户端里该策略未启动；计划未生成 → 看 09:36/09:40 计划任务日志。"
    try:
        import subprocess
        env = dict(os.environ)
        env["LARK_SRC_TAG"] = "[QD]"
        subprocess.call([sys.executable, os.path.join(PROJ, "push_alert_card.py"),
                         "--title", "P16 链路巡检异常", "--body", body, "--level", "warn"],
                        cwd=PROJ, env=env, timeout=60)
    except Exception as e:
        print("  [PUSH-FAIL] %s" % (str(e)[:120],))
    return 2


if __name__ == "__main__":
    sys.exit(main())
