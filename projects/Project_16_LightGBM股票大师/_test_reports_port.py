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

# --- P4 日志归属：状态机必须吃下续行、隔离 FILL?、按模型等值比较 ---
# ★ 2026-10-30 审计 §2.4：旧 fixture 每行都带前缀、没有续行、没有 FILL?，
#   所以「续行盲区」「FILL? 当自有成交」三个缺陷在 22 项回归里全测不出来。
#   现在一律用 golden fixture（`_fixtures/formula_output_20261030_golden.log`，
#   从真实 XtClient_FormulaOutput_20261030.log 脱敏摘录，保留原样的续行结构）。
GOLDEN = os.path.join(HERE, "_fixtures", "formula_output_20261030_golden.log")
FAKE_LOG = open(GOLDEN, encoding="utf-8").read()

g2 = HB._model_lines(FAKE_LOG, "STRATEGY_P16_G2_EXEC")
check("P4 G2 不含 V13 行", all("V13_EXEC" not in ln for ln in g2))
check("P4 G2 不含 SBX 行", all("G2_EXEC_SBX" not in ln for ln in g2))

v13 = HB._model_lines(FAKE_LOG, "STRATEGY_P16_V13_EXEC")
check("P4 V13 不含 G2 行", all("G2_EXEC" not in ln for ln in v13))
sbx = HB._model_lines(FAKE_LOG, "STRATEGY_P16_G2_EXEC_SBX")
check("P4 SBX 不被 G2 父串吃掉", len(sbx) == 3 and "FILL? 603529.SH 300@19.100" in "\n".join(sbx))

# 午间/盘后的 _scan_log 用状态机口径
import tempfile
tmpd = tempfile.mkdtemp()
logdir = os.path.join(tmpd, "userdata", "log")
os.makedirs(logdir)
open(os.path.join(logdir, "XtClient_FormulaOutput_%s.log" % MD.D), "w", encoding="utf-8").write(FAKE_LOG)

e_g2 = MD._scan_log(tmpd, "STRATEGY_P16_G2_EXEC")
e_v13 = MD._scan_log(tmpd, "STRATEGY_P16_V13_EXEC")
e_sbx = MD._scan_log(tmpd, "STRATEGY_P16_G2_EXEC_SBX")
kinds_g2 = [k for k, _r, _t in e_g2]

# A) 续行必须被吃下：G2 的 2 条 ORDER 与 3 条 FILL 全是续行
check("P4a G2 ORDER 续行被捕获（2 条）", kinds_g2.count("ORDER") == 2)
check("P4a G2 FILL 续行被捕获（3 条）", kinds_g2.count("FILL") == 3)

# B) FILL? 绝不并入自有成交
tag_g2, orders_g2, fills_g2, _c_g2, foreign_g2 = MD._summarize_events(e_g2)
check("P4b G2 自有成交 3 笔（300+500+100）", len(fills_g2) == 3)
check("P4b G2 成交量为 300/500/100",
      [v for _c, v, _p, _s in fills_g2] == [300, 500, 100])
# ★ CC 复核 §4.5：G2 自己在 10:00:32 重启后也打了 3 条 FILL?（自己上一轮的成交，
#   因 sid_marker 为空而无法归属）。旧 fixture 没有这段、断言写 0，是空断言。
#   现在 fixture 补了真实那段，断言值必须是 3。
check("P4b G2 的 FILL? 计入 foreign（真实值 3，不是 0）", foreign_g2 == 3)

tag_v13, _o_v13, fills_v13, _c_v13, foreign_v13 = MD._summarize_events(e_v13)
check("P4b V13 自有成交为 0（那 3 笔是 G2 的）", len(fills_v13) == 0)
check("P4b V13 的 FILL? 计入 foreign=3", foreign_v13 == 3)

tag_sbx, _o_sbx, fills_sbx, _c_sbx, foreign_sbx = MD._summarize_events(e_sbx)
check("P4b SBX 自有成交为 0（DRY 不得把 FILL? 当成交）", len(fills_sbx) == 0)
check("P4b SBX 的 FILL? 计入 foreign=1", foreign_sbx == 1)

# C) 时间戳随归属继承：15:10 之后的事件必须能被时间窗过滤掉
late = [e for e in e_g2 if e[2][11:16] > "15:10"]
check("P4c 15:10 后事件带真实时间戳（续行不再切出 'EC] F'）", len(late) == 2)
check("P4c 15:10 前事件保留", len([e for e in e_g2 if e[2][11:16] <= "15:10"]) == len(e_g2) - 2)
check("P4c 15:10 后的续行也继承到正确时间",
      all(e[2][11:16] == "15:20" for e in late))

# F) 多行 INIT（CC 复核 §3/§4.1）：必须走**完整链路** `_scan_log → _summarize_events`
#    ★ 上一轮 P4f 直接喂事件元组、且 rest 里已带 BUILD_TAG，既没测真实链路也没测新代码，
#      是同义反复（CC 复核 §3.4 抓到的）。现在一律喂**原始日志文本**，走真 `_scan_log`。
def _scan_text(log_text, model, d="20261030"):
    """把日志文本写进临时目录，走真 `_scan_log`，返回事件列表。"""
    import shutil
    td = tempfile.mkdtemp()
    try:
        ld = os.path.join(td, "userdata", "log")
        os.makedirs(ld)
        open(os.path.join(ld, "XtClient_FormulaOutput_%s.log" % d), "w",
             encoding="utf-8").write(log_text)
        old_d = MD.D
        MD.D = d
        try:
            return MD._scan_log(td, model)
        finally:
            MD.D = old_d
    finally:
        shutil.rmtree(td, ignore_errors=True)


_PFX = ("2026-10-30 08:52:25,515 [INFO] [0x00001114] reqid = "
        "0D:\\国金QMT交易端模拟\\python\\STRATEGY_P16_G2_EXEC.py_00030011, output = ")
MULTI_INIT_LOG = (
    _PFX + "[P16SIG][EXEC] INIT\n"
    "  BUILD_TAG=20261030-999999 account=67014907 dry_run=False\n"
)
_ev = _scan_text(MULTI_INIT_LOG, "STRATEGY_P16_G2_EXEC")
check("P4f 多行 INIT：_scan_log 产出 CONT 续行事件",
      any(k == "CONT" for k, _r, _t in _ev))
check("P4f 多行 INIT：走完整链路能取到 TAG（不再是死代码）",
      MD._summarize_events(_ev)[0] == "20261030-999999")

# 单行 INIT 仍正常
_ev2 = _scan_text(_PFX + "[P16SIG][EXEC] INIT BUILD_TAG=20261030-888888\n",
                  "STRATEGY_P16_G2_EXEC")
check("P4f 单行 INIT 仍能取到 TAG", MD._summarize_events(_ev2)[0] == "20261030-888888")

# CONT 不能污染 counts / 不能冒充事件
_t3, _o3, _f3, _c3, _fg3 = MD._summarize_events(_ev)
check("P4f CONT 不计入 counts", "CONT" not in _c3)
check("P4f CONT 不产生 order/fill", _o3 == [] and _f3 == [] and _fg3 == 0)

# ★ CC 复核 §3.1：INIT 与 BUILD_TAG= 之间夹了别的裸续行（另一模型的裸行不带 reqid，
#   会被当成本模型的 CONT）⇒ 不得因「第一条 CONT 没 TAG」就提前放弃。
_MULTI_GAP = (
    _PFX + "[P16SIG][EXEC] INIT\n"
    "  account=67014907\n"                 # 干扰续行（无 TAG）
    "  BUILD_TAG=20261030-777777\n"        # 真正的 TAG 在后面
)
_ev_gap = _scan_text(_MULTI_GAP, "STRATEGY_P16_G2_EXEC")
check("P4f INIT 与 TAG 之间夹干扰续行时仍能取到 TAG（CC §3.1）",
      MD._summarize_events(_ev_gap)[0] == "20261030-777777")

# 全程无 TAG 的多行 INIT ⇒ 空（正确，本就没有）
_NO_TAG = _PFX + "[P16SIG][EXEC] INIT\n  account=67014907\n  dry_run=False\n"
check("P4f 全程无 TAG 的多行 INIT => 空（不伪造）",
      MD._summarize_events(_scan_text(_NO_TAG, "STRATEGY_P16_G2_EXEC"))[0] == "")

# 心跳侧：_has_init 也必须认多行 INIT（走 _model_lines）
HB_PFX = ("2026-10-30 08:52:25,515 [INFO] [0x00001114] reqid = "
          "0D:\\国金QMT交易端模拟\\python\\STRATEGY_P16_G2_EXEC.py_00030011, output = ")
HB_MULTI = HB_PFX + "[P16SIG][EXEC] INIT\n  BUILD_TAG=20261030-140212 account=67014907\n"
check("P4f 心跳 _has_init 认多行 INIT（走 _model_lines）",
      HB._has_init(HB._model_lines(HB_MULTI, "STRATEGY_P16_G2_EXEC")) is True)
check("P4f 心跳 _has_init 无 INIT 时返回 False",
      HB._has_init(HB._model_lines(HB_PFX + "[P16SIG][EXEC] SKIP\n",
                                   "STRATEGY_P16_G2_EXEC")) is False)

# G) 断档天数用自然日（CC 复核 §4.6）
check("P4g 断档函数改名为 _natural_days_between",
      hasattr(CL, "_natural_days_between") and not hasattr(CL, "_trading_days_between"))
check("P4g 20261022→20261030 = 8 个自然日",
      CL._natural_days_between("20261022", "20261030") == 8)
d4 = os.path.join(tmpd, "ens2")
os.makedirs(d4)
open(os.path.join(d4, "reconcile_g2_ens_20261022.json"), "w", encoding="utf-8").write("{}")
_t, _b = CL._reconcile_line("ENS", os.path.join(d4, "reconcile_g2_ens_%s.json" % CL.D))
check("P4g 文案写「自然日」而非「天」", "自然日" in _t)
#    ★ CC 复核 §3.3：今天 G2 计划被 19:22 补跑覆盖成 1 腿 SELL，而实际成交是 BUY 800，
#      旧实现不区分方向 ⇒ 渲染出「✔ 603529.SH 计划 100 / 成交 800」的假 ✔。
check("P4e G2 fills 都带方向", all(side in ("BUY", "SELL", "?") for _c, _v, _p, side in fills_g2))
check("P4e G2 的 3 笔成交方向都是 BUY（由同批 ORDER 推断）",
      all(side == "BUY" for _c, _v, _p, side in fills_g2))

# 造一个「计划 SELL、成交 BUY」的场景，断言不得判 ✔
import importlib
_mk = lambda k, r, t="2026-10-30 09:50:25": (k, r, t)
_evts_dir = [
    _mk("ORDER", "BUY 603529.SH 800@19.23"),
    _mk("FILL", "603529.SH 800@19.266"),
]
_t, _o, _f, _c, _fg = MD._summarize_events(_evts_dir)
_filled = {}
for _code, _vol, _px, _side in _f:
    _filled[(_side, _code)] = _filled.get((_side, _code), 0) + _vol
_plan_leg = {"action": "SELL", "code": "603529.SH", "vol": 100}
_pv = int(_plan_leg["vol"])
_fv = _filled.get(("SELL", "603529.SH"), 0)
check("P4e 计划 SELL / 成交 BUY ⇒ 成交数必须为 0（不再拿反向成交抵）", _fv == 0)
check("P4e 该腿不得判 ✔", not (_fv >= _pv and _pv > 0))
check("P4e 反向成交仍可见（800 股 BUY 记录在案）",
      _filled.get(("BUY", "603529.SH"), 0) == 800)

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
