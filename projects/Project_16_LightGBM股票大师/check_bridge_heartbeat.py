# coding: utf-8
"""P16 链路健康巡检（原「桥心跳超时告警」，2026-10-29 重写；2026-10-30 改单端口多路由）。

**为什么重写**：旧版盯 `D:/QMT_POOL/*/state/heart_*.json`——文件桥 2026-10-15 起作废、
心跳文件再没人写（ENS/V13 的停在 10-16），于是每次巡检必报「心跳超时 112 万秒」这种
**结构性假警**（2026-10-29 15:00 实测）。现在的下单链路是「外部计划 → 回环 hub → 内置执行壳
passorder」，心跳该看的东西全变了。

**为什么改端口（2026-10-30 实测）**：QMT 客户端 `python_anti_plugin_monitor` 只放行白名单里的
回环端口，本机可用端口只有 **58610**；连 5871x 会被判 `illegal ip` 并 `try stop` 策略。
所以 hub 收成**单实例 58610 + 路径路由**（`/signal/g2`、`/signal/g2sbx`、`/signal/v13`）。
旧版这里硬编码 58710/58711/58712，在 hub 改端口后**每天固定推三条假警**
（2026-10-30 14:00 实测：三个端口全报未监听，而 `/health` 三路由其实全 ok）。

**现在检查什么（交易日 09:30–15:10 窗口内）**：
  1. 回环 hub 58610 是否在听，且 `/health` 里 g2 / g2sbx / v13 三条路由各自 ok；
  2. 各执行壳今天是否有 `[P16SIG][EXEC] INIT BUILD_TAG=`（**按模型名过滤**——日志是各策略
     混写的，不过滤则任一策略起来就全判「已加载」）；
  3. 09:45 之后是否已生成当天计划文件（hub 的输入）；
  4. 执行窗口内是否出现过 `[P16SIG][EXEC] PLAN`（= 真拿到了信号，不是空转）。

**ENS 不再监控**：它的 xtquant 外部腿在 2.1.33 上已不可用（`XtMiniQmt.exe` 不再自带），
本条只留一行 SKIP 说明，不当故障报。

退出码：0 正常 / 2 发现异常（已告警）。
"""
from __future__ import print_function

import json
import os
import re
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
HUB_PORT = 58610             # 白名单内唯一可用的回环端口（改前先 grep 客户端日志 whitelist）

# 归属标记：reqid 行里的 `...\python\<模型>.py_NNN`。取模型名做等值比较，不用子串。
MODEL_RE = re.compile(r"\\python\\([A-Za-z0-9_]+)\.py_\d+")

# 逻辑名 -> (客户端目录, 模型名, hub 路由 key, 计划目录, 计划文件名模板)
SHELLS = {
    "G2-main": (r"D:\国金QMT交易端模拟", "STRATEGY_P16_G2_EXEC", "g2",
                os.path.join(PROJ, "data", "rebalance_g2"), "rebalance_g2_%s.plan.json"),
    "G2-sbx": (r"D:\QMT交易端模拟", "STRATEGY_P16_G2_EXEC_SBX", "g2sbx",
               os.path.join(PROJ, "data", "rebalance_g2", "sbx"), "rebalance_g2_%s.json"),
    "V13-main": (r"D:\国金QMT交易端模拟", "STRATEGY_P16_V13_EXEC", "v13",
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


def _hub_health(port, timeout=3.0):
    """GET /health → {"ok":bool, "routes":{key:(ok,reason,plan)}}。

    只用标准库；任何异常返回 None（调用方据此报「hub 未听/不可达」）。
    """
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
    routes = {}
    for r in data.get("routes") or []:
        routes[str(r.get("key"))] = (bool(r.get("ok")), str(r.get("reason") or ""),
                                     str(r.get("plan") or ""))
    return {"ok": bool(data.get("ok")), "routes": routes}


def _read_log(client_dir, date):
    p = os.path.join(client_dir, "userdata", "log", "XtClient_FormulaOutput_%s.log" % date)
    if not os.path.exists(p):
        return "", p
    try:
        return open(p, "rb").read().decode("utf-8", "replace"), p
    except Exception:
        return "", p


def _model_lines(txt, model):
    """按「归属态」取属于本模型的日志行（含续行）。

    日志是**各策略混写**在同一个 FormulaOutput 文件里，格式为「一条 print 的首行带
    `reqid = 0<路径>\\python\\<模型>.py_NNN, output = ` 前缀，多行 print 的后续行裸写」。

    ★ 2026-10-30 修（审计 §2.5）：旧实现用 `模型名.py in line` 子串过滤，有两个毛病：
      ① 只能命中每次打印的首行 —— 续行（裸写）全丢；
      ② `_G2_EXEC` 会吃掉 `_G2_EXEC_SBX`（子串匹配没有边界）。
    现改为：用正则取模型名做**等值比较**，并让续行继承上一条归属。
    今天 INIT 恰好都带前缀所以旧实现"看起来对"，但那是运气——INIT 一旦打印成多行就会误判 NOT_RUNNING。
    """
    owner = ""
    out = []
    for ln in txt.splitlines():
        m = MODEL_RE.search(ln)
        if m:
            owner = m.group(1)
        if owner == model:
            out.append(ln)
    return out


def _has_init(lines):
    """是否出现过 INIT。

    ★ 2026-10-31 CC 复核 §7.1：早期版本写了三个分支（本行有 TAG / 下一行有 TAG / 都没有）
    但**三条路径全返回 True**，等价于 `any("[P16SIG][EXEC] INIT" in ln)`。前两条是装饰性的，
    会让读者以为多行 INIT 真的做了判断。压成一行并说明取舍：
      · 这里只判「INIT 是否出现过」——多行 INIT（首行 `INIT`、次行 `BUILD_TAG=`）两种形态
        都会命中 `INIT`，因此不需要分支；
      · TAG 的**具体取值**核对交给 `check_running_tag.py`（那边按归属态解析、支持多行）。
    """
    return any("[P16SIG][EXEC] INIT" in ln for ln in lines)


def check_shell(name, client_dir, model, route_key, plan_dir, plan_tpl, health):
    """返回 (level, 说明)；level: OK / ALERT / WARN。"""
    date = _today()
    txt, logp = _read_log(client_dir, date)
    lines = _model_lines(txt, model)
    inited = _has_init(lines)
    planned = any("[P16SIG][EXEC] PLAN" in ln for ln in lines)

    problems = []
    if health is None:
        problems.append("hub %d 未监听或 /health 不可达" % HUB_PORT)
    elif route_key not in health["routes"]:
        problems.append("hub /health 缺路由 %s" % route_key)
    else:
        ok, reason, _plan = health["routes"][route_key]
        if not ok:
            # 路由 not-ok 通常就是「今天没有计划」或「计划日期不是今天」，直接给原因
            problems.append("hub 路由 %s 无信号（%s）" % (route_key, reason or "原因未给"))
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
    if inited:
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

    # hub 只查一次（单实例多路由），三条 shell 共用结果
    if _port_open(HUB_PORT):
        health = _hub_health(HUB_PORT)
        if health is None:
            print("  [WARN] hub 端口 %d 在听但 /health 无响应" % HUB_PORT)
        else:
            keys = sorted(health["routes"].keys())
            bad = [k for k in keys if not health["routes"][k][0]]
            print("  [HUB] %d 路由=%s%s" % (HUB_PORT, ",".join(keys),
                                          (" 无信号=%s" % ",".join(bad)) if bad else " 全部 ok"))
    else:
        health = None
        print("  [ALERT] hub %d 未监听（scripts/qmt_signal_hub.ps1 -Mode Start）" % HUB_PORT)

    for name, args in SHELLS.items():
        level, msg = check_shell(name, args[0], args[1], args[2], args[3], args[4], health)
        print("  [%s] %-9s %s" % (level, name, msg))
        if level == "ALERT":
            alerts.append("%s: %s" % (name, msg))
    print("  [SKIP] ENS      已下线（xtquant 在 2.1.33 不可用，无外部下单腿）")

    if not alerts:
        print("[CHAIN] 全部正常")
        return 0

    body = ("链路巡检发现异常（%s）：\n" % time.strftime("%H:%M")) + "\n".join("- " + a for a in alerts) + \
           "\n\n排查方向：hub 未听 → `scripts/qmt_signal_hub.ps1 -Mode Start`；" \
           "路由无信号 → 看计划文件日期/是否当天生成；" \
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
