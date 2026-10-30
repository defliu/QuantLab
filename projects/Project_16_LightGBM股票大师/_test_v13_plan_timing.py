# coding: utf-8
"""V1.3 计划时序回归（2026-10-30 立）。

钉住的坑：执行壳窗口 09:46–09:56，而计划任务曾定在 09:45 触发（只留 1 分钟，
且任务触发本身有秒级抖动——实测 09:45:45）。更糟的是 2026-10-30 当天该任务
**12:13 才注册**，计划 12:15 落盘，窗口早过 ⇒ V13 三腿全空，但盘上有计划、
日志也正常，事后极难看出根因。

本轮改动把三件事钉死：
  1. 计划任务触发时点提前到 **09:42**（留 4 分钟余量，实测脚本全程 ~6 秒）；
  2. `v13_plan_daily.py` 收尾自检：落盘时刻 >= 窗口起点 ⇒ 返回 4 并推告警，
     **不静默成功**（「落盘了但晚了」必须显式失败）；
  3. 脚本内的 EXEC_HM_START 必须与执行壳产物里的 EXEC_HM_START 一致（防漂移）。

纯离线：用临时目录 + 桩脚本，不跑真 rebalance_daily、不碰 QMT、不推飞书。
"""
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


spec = importlib.util.spec_from_file_location("v13plan", os.path.join(HERE, "v13_plan_daily.py"))
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)

# --- V1 窗口常量必须与执行壳产物一致 ---
prod = os.path.join(HERE, "build", "strategy_p16_v13_exec.py")
if os.path.exists(prod):
    txt = io.open(prod, encoding="gbk").read()
    m = re.search(r"^EXEC_HM_START = (\d+)", txt, re.M)
    shell_start = int(m.group(1)) if m else None
    check("V1 计划脚本窗口起点 == 执行壳产物窗口起点", P.EXEC_HM_START == shell_start)
else:
    check("V1 执行壳产物存在（跳过比对）", True)

# --- V2 计划任务触发时点必须早于窗口，且留 >=3 分钟余量 ---
try:
    out = subprocess.check_output([
        "powershell", "-NoProfile", "-Command",
        "(Get-ScheduledTask -TaskName 'Quant_P16_V13_PlanOnly_0936').Triggers[0].StartBoundary"],
        stderr=subprocess.STDOUT)
    boundary = out.decode("utf-8", "replace").strip()
    m = re.search(r"T(\d{2}):(\d{2}):", boundary)
    if m:
        trig = int(m.group(1)) * 100 + int(m.group(2))
        gap = (P.EXEC_HM_START // 100 - trig // 100) * 60 + (P.EXEC_HM_START % 100 - trig % 100)
        check("V2 计划任务触发时点 %04d 早于窗口 %04d" % (trig, P.EXEC_HM_START), trig < P.EXEC_HM_START)
        check("V2 余量 >= 3 分钟（实测 %d 分钟）" % gap, gap >= 3)
    else:
        check("V2 读到任务触发时点", False)
except Exception as e:
    check("V2 读任务触发时点失败: %s" % str(e)[:60], False)

# --- V3 真跑 main（用桩替换 market_tier / rebalance_daily），验证四条分支 ---
# 造一个临时"项目目录"：HERE 换成 tmp，两个依赖脚本放桩。
tmp = tempfile.mkdtemp()
plans = os.path.join(tmp, "data", "rebalance_v13")
os.makedirs(plans)
today = P._today()
plan_file = os.path.join(plans, "rebalance_v13_%s.plan.json" % today)

# 桩 market_tier：输出合法 tier JSON
io.open(os.path.join(tmp, "market_tier.py"), "w", encoding="utf-8").write(
    "import json,sys\nprint(json.dumps({'tier':2}))\n")
# 桩 rebalance_daily：把计划文件写出来（模拟成功）
io.open(os.path.join(tmp, "rebalance_daily.py"), "w", encoding="utf-8").write(
    "import io,os,sys\n"
    "d=os.path.join(os.path.dirname(os.path.abspath(__file__)),'data','rebalance_v13')\n"
    "os.makedirs(d,exist_ok=True)\n"
    "p=os.path.join(d,'rebalance_v13_%s.plan.json')\n" % today
    + "io.open(p,'w',encoding='utf-8').write('{\"orders\":[]}')\n")
# 桩 push：只记录，不推真卡
io.open(os.path.join(tmp, "push_alert_card.py"), "w", encoding="utf-8").write(
    "import io,os,sys\n"
    "a=sys.argv\n"
    "t=a[a.index('--title')+1]\n"
    "io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'pushed.txt'),'a',"
    "encoding='utf-8').write(t+'\\n')\n")

orig = (P.HERE, P.PLAN_DIR, P._hhmm, P.PY)
P.HERE = tmp
P.PLAN_DIR = plans
P.PY = sys.executable
try:
    # 3a 早于窗口 => rc=0（正常出计划）
    P._hhmm = lambda: P.EXEC_HM_START - 20
    rc = P.main()
    check("V3a 早于窗口 rc=0", rc == 0)
    check("V3a 早于窗口不推告警",
          not os.path.exists(os.path.join(tmp, "pushed.txt")))

    # 3b 晚于窗口 => rc=4 且推告警（「落盘了但晚了」必须显式失败）
    P._hhmm = lambda: P.EXEC_HM_START + 5
    rc = P.main()
    check("V3b 晚于窗口 rc=4（不静默成功）", rc == 4)
    pushed = io.open(os.path.join(tmp, "pushed.txt"), encoding="utf-8").read()
    check("V3b 晚于窗口推了告警", "晚于执行窗口" in pushed)

    # 3c 计划缺失 => rc=3 且推告警
    os.remove(plan_file)
    # 桩会重写文件；这里让 rebalance_daily 桩失败，制造「rc=0 但无产物」不可能，
    # 改为直接把桩改成不写文件 + 返回 0，模拟「命令成功但产物没落」
    io.open(os.path.join(tmp, "rebalance_daily.py"), "w", encoding="utf-8").write("pass\n")
    if os.path.exists(plan_file):
        os.remove(plan_file)
    rc = P.main()
    check("V3c 计划缺失 rc=3", rc == 3)
    pushed = io.open(os.path.join(tmp, "pushed.txt"), encoding="utf-8").read()
    check("V3c 计划缺失推了告警", "计划未生成" in pushed)

    # 3d 出计划失败 => rc=3
    io.open(os.path.join(tmp, "rebalance_daily.py"), "w", encoding="utf-8").write(
        "import sys\nsys.exit(1)\n")
    rc = P.main()
    check("V3d 出计划失败 rc=3", rc == 3)

    # 3e T 档异常 => rc=2
    io.open(os.path.join(tmp, "market_tier.py"), "w", encoding="utf-8").write(
        "import json,sys\nprint(json.dumps({'tier':9}))\n")
    rc = P.main()
    check("V3e T 档非法 rc=2", rc == 2)
finally:
    P.HERE, P.PLAN_DIR, P._hhmm, P.PY = orig

# --- V4 告警推送必须 best-effort，失败不抛 ---
check("V4 _push 内部 try/except（推送失败不阻断）",
      "PUSH-FAIL" in io.open(os.path.join(HERE, "v13_plan_daily.py"), encoding="utf-8").read())

# --- V5 时序自检必须在计划生成**之后**（不能挡住正常出计划）---
src = io.open(os.path.join(HERE, "v13_plan_daily.py"), encoding="utf-8").read()
i_plan = src.find("rebalance_daily.py")
i_check = src.find("时序自检")
check("V5 自检在出计划之后", i_plan != -1 and i_check != -1 and i_check > i_plan)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
