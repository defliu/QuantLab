# coding=utf-8
"""链路巡检 / 午间 / 盘后 三个报告脚本的端口与日志过滤回归（2026-10-30 立）。

钉住的两个坑：
  1. **端口漂移**：hub 收成单实例 58610 + 路径路由后，三个脚本仍硬编码 58710/58711/58712
     => 每天固定推「hub 未监听」假警（2026-10-30 14:00 实测：三端口全报未监听，
     而 /health 三路由其实全 ok）。
  2. **日志串读**：FormulaOutput 是各策略混写的一个文件。不过滤则
     - 心跳巡检：任一策略起来就全判「已加载」，「策略未启动」永不触发；
     - 午间/盘后：V13 面板把 G2 的成交算成自己的。
     用「模型名.py」精确匹配，且不能被 `..._G2_EXEC` 吃掉 `..._G2_EXEC_SBX`。

纯离线：monkeypatch 端口探测与日志读取，不连 hub、不碰 QMT。
"""
import importlib.util
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


def load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(mod_name, os.path.join(HERE, filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


HB = load("hb", "check_bridge_heartbeat.py")
MD = load("md", "p16_midday_report.py")
CL = load("cl", "p16_close_report.py")

# --- P1 端口常量必须收敛到 58610 ---
check("P1 心跳巡检 HUB_PORT == 58610", HB.HUB_PORT == 58610)
check("P1 午间报告 HUB_PORT == 58610", MD.HUB_PORT == 58610)
check("P1 盘后报告复用午间 HUB_PORT", CL.M.HUB_PORT == 58610)

# --- P2 三个脚本里不得再出现 5871x 作为**实际探测端口** ---
def _no_stale_port(mod, fname):
    src = open(os.path.join(HERE, fname), encoding="utf-8").read()
    hits = []
    for ln in src.splitlines():
        s = ln.strip()
        if s.startswith("#") or s.startswith('"""') or s.startswith("旧版") or s.startswith("（"):
            continue
        if any(p in s for p in ("58710", "58711", "58712")) and "假警" not in s:
            hits.append(s)
    return hits


check("P2 心跳巡检无残留旧端口", _no_stale_port(HB, "check_bridge_heartbeat.py") == [])
check("P2 午间报告无残留旧端口", _no_stale_port(MD, "p16_midday_report.py") == [])
check("P2 盘后报告无残留旧端口", _no_stale_port(CL, "p16_close_report.py") == [])

# --- P3 三条 shell 各自带路由 key 与模型名 ---
check("P3 心跳三条 shell 都带路由 key", all(len(v) == 5 for v in HB.SHELLS.values()))
check("P3 心跳路由 key 集合正确",
      set(v[2] for v in HB.SHELLS.values()) == {"g2", "g2sbx", "v13"})
check("P3 午间三条 shell 都带路由 key + 模型名", all(len(v) == 6 for v in MD.SHELLS))
check("P3 午间路由 key 集合正确", set(v[3] for v in MD.SHELLS) == {"g2", "g2sbx", "v13"})

# --- P4 日志过滤：必须按模型名精确区分 ---
FAKE_LOG = "\n".join([
    "2026-10-30 09:50:01 x python\\STRATEGY_P16_G2_EXEC.py_001, output = [P16SIG][EXEC] INIT BUILD_TAG=20261030-140212",
    "2026-10-30 09:50:02 x python\\STRATEGY_P16_G2_EXEC.py_001, output = [P16SIG][EXEC] PLAN ok sid=g2-1 n=2",
    "2026-10-30 09:50:03 x python\\STRATEGY_P16_G2_EXEC.py_001, output = [P16SIG][EXEC] FILL 603529.SH 300@19.26",
    "2026-10-30 09:50:04 x python\\STRATEGY_P16_V13_EXEC.py_002, output = [P16SIG][EXEC] INIT BUILD_TAG=20261030-140213",
    "2026-10-30 09:50:05 x python\\STRATEGY_P16_V13_EXEC.py_002, output = [P16SIG][EXEC] SKIP 订单为空",
    # 沙箱名含父串，必须不被 G2 吃掉
    "2026-10-30 09:50:06 x python\\STRATEGY_P16_G2_EXEC_SBX.py_003, output = [P16SIG][EXEC] INIT BUILD_TAG=20261030-140219",
])

g2 = HB._model_lines(FAKE_LOG, "STRATEGY_P16_G2_EXEC")
check("P4 G2 行数只含自己的 3 行（不吃沙箱）", len(g2) == 3)
check("P4 G2 不含 V13 行", all("V13_EXEC" not in ln for ln in g2))
check("P4 G2 不含 SBX 行", all("G2_EXEC_SBX" not in ln for ln in g2))

v13 = HB._model_lines(FAKE_LOG, "STRATEGY_P16_V13_EXEC")
check("P4 V13 只看到自己的 2 行", len(v13) == 2)

sbx = HB._model_lines(FAKE_LOG, "STRATEGY_P16_G2_EXEC_SBX")
check("P4 SBX 只看到自己的 1 行", len(sbx) == 1)

# 午间/盘后的 _scan_log 用同样口径
import tempfile
tmpd = tempfile.mkdtemp()
logdir = os.path.join(tmpd, "userdata", "log")
os.makedirs(logdir)
open(os.path.join(logdir, "XtClient_FormulaOutput_%s.log" % MD.D), "w", encoding="utf-8").write(FAKE_LOG)

e_g2 = MD._scan_log(tmpd, "STRATEGY_P16_G2_EXEC")
e_v13 = MD._scan_log(tmpd, "STRATEGY_P16_V13_EXEC")
kinds_g2 = [k for k, _r, _t in e_g2]
kinds_v13 = [k for k, _r, _t in e_v13]
check("P4 午间 G2 事件 INIT/PLAN/FILL 各 1", kinds_g2 == ["INIT", "PLAN", "FILL"])
check("P4 午间 V13 事件不含 G2 的 FILL", kinds_v13 == ["INIT", "SKIP"])
check("P4 午间 G2 的 tag 是 G2 自己的",
      MD._summarize_events(e_g2)[0] == "20261030-140212")
check("P4 午间 V13 的 tag 是 V13 自己的",
      MD._summarize_events(e_v13)[0] == "20261030-140213")

# --- P5 hub 健康解析：路由不 ok 必须能给出原因 ---
orig_hh = HB._hub_health
HB._hub_health = lambda port, timeout=3.0: {"ok": True, "routes": {
    "g2": (True, "", "p"), "g2sbx": (False, "plan_date_not_today:20261029", "p"),
    "v13": (True, "", "p")}}
try:
    lvl, msg = HB.check_shell("G2-sbx", tmpd, "STRATEGY_P16_G2_EXEC_SBX", "g2sbx",
                              os.path.join(tmpd, "none"), "x_%s.json", HB._hub_health(58610))
    check("P5 路由无信号判 ALERT", lvl == "ALERT")
    check("P5 路由无信号给出原因", "plan_date_not_today" in msg)
finally:
    HB._hub_health = orig_hh

# 午间报告的 hub 解析用同一形状
check("P5 午间 _hub_health 返回 {key:(ok,reason)}",
      isinstance(MD._hub_health, type(MD._hub_health)))

# --- P6 hub 整体不可达 => 三条都 ALERT，且不是「端口未监听」误报 ---
orig_hh2 = HB._hub_health
HB._hub_health = lambda port, timeout=3.0: None
try:
    lvl, msg = HB.check_shell("V13-main", tmpd, "STRATEGY_P16_V13_EXEC", "v13",
                              os.path.join(tmpd, "none"), "x_%s.json", None)
    check("P6 hub 不可达判 ALERT", lvl == "ALERT")
    check("P6 提示 /health 不可达而非伪造端口", "不可达" in msg)
finally:
    HB._hub_health = orig_hh2

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
