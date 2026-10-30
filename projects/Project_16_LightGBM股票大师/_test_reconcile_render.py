# coding: utf-8
"""对账渲染口径回归（2026-10-30 立，审计 §3）。

钉住的三个坑（都是「失败被渲染成正常」）：
  1. `reconcile_<date>.json` 是 `list[2]`，两条都是 `{"connected": false, "error": "连接 miniQMT 失败 rc=-1"}`，
     旧实现走 `isinstance(d, list) → "同步 %d 条"`，读成「同步 2 条」（实际是 2 条连接失败）。
  2. 产物缺失只写「产物缺失」，不区分「今天没跑」与「已经断了一周」
     （ENS 实测断 6–8 个交易日，每天一行看起来像单日偶发）。
  3. `reconcile_g2` 的成交/账本/持仓三者全空时，旧实现渲染成「成交 0｜孤儿 0｜issues 0」全绿，
     而实际是数据源（文件桥 state 目录）早已作废 —— 「账号不可读」≠「空仓」。

纯离线：临时目录造假产物，不跑真对账、不碰 QMT。
"""
import importlib.util
import io
import json
import os
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


spec = importlib.util.spec_from_file_location("cl", os.path.join(HERE, "p16_close_report.py"))
CL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CL)

D = CL.D
tmp = tempfile.mkdtemp()


def write(name, obj):
    p = os.path.join(tmp, name)
    io.open(p, "w", encoding="utf-8").write(json.dumps(obj, ensure_ascii=False))
    return p


# --- R1 list 形态 + connected:false ⇒ 必须渲染成「连接失败」，且判异常 ---
p = write("reconcile_%s.json" % D, [
    {"strategy": "g2", "connected": False, "error": "RuntimeError('连接 miniQMT 失败 rc=-1')"},
    {"strategy": "ens", "connected": False, "error": "RuntimeError('连接 miniQMT 失败 rc=-1')"},
])
txt, bad = CL._reconcile_line("V13", p)
check("R1 连接失败不再渲染成「同步 2 条」", "同步" not in txt)
check("R1 明确写「连接失败」", "连接失败" in txt)
check("R1 标出 2/2 全部失败", "2/2" in txt)
check("R1 判为异常（计入 problems）", bad is True)

# --- R2 list 形态 + 全部 connected:true ⇒ 正常 ---
p = write("ok_%s.json" % D, [{"strategy": "g2", "connected": True}])
txt, bad = CL._reconcile_line("同步成交", p)
check("R2 全部连接成功才渲染「同步 N 条」", "同步 1 条" in txt)
check("R2 不判异常", bad is False)

# --- R3 空列表 ⇒ 异常（0 条记录 ≠ 正常）---
p = write("empty_%s.json" % D, [])
txt, bad = CL._reconcile_line("X", p)
check("R3 空产物判异常", bad is True and "空产物" in txt)

# --- R4 产物缺失 + 有历史 ⇒ 必须报「已断 N 天」---
d2 = os.path.join(tmp, "ens")
os.makedirs(d2)
io.open(os.path.join(d2, "reconcile_g2_ens_20261022.json"), "w", encoding="utf-8").write("{}")
txt, bad = CL._reconcile_line("ENS", os.path.join(d2, "reconcile_g2_ens_%s.json" % D))
check("R4 产物缺失标异常", bad is True)
check("R4 给出最近产物日期", "20261022" in txt)
check("R4 给出断档天数", "已断" in txt and "自然日" in txt)
check("R4 不再只是「产物缺失」四个字", "最近产物" in txt)

# --- R5 产物缺失且目录全空 ⇒ 明确说无历史 ---
d3 = os.path.join(tmp, "empty_dir")
os.makedirs(d3)
txt, bad = CL._reconcile_line("Y", os.path.join(d3, "nope_%s.json" % D))
check("R5 无历史产物明确标注", bad is True and "无任何历史产物" in txt)

# --- R6 reconcile_g2 全空（成交/账本/持仓都空）⇒ 不得渲染成干净 ---
p = write("g2empty_%s.json" % D, {
    "date": D, "fills": [], "pending": [], "orphans": [], "ledger": {},
    "asset": {}, "account_positions": {}, "issues": [], "fills_total": 0,
    "snapshot_missing": False,
})
txt, bad = CL._reconcile_line("G2", p)
check("R6 三者全空判异常（不再全绿）", bad is True)
check("R6 标出「三者全空」", "三者全空" in txt)

# --- R6b source_unavailable=True 必须**被渲染消费**（CC 复核 §4.2：不能只写不读）---
p = write("g2src_%s.json" % D, {
    "date": D, "fills": [], "pending": [], "orphans": [], "ledger": {},
    "account_positions": {}, "issues": [], "fills_total": 0,
    "snapshot_missing": True, "source_unavailable": True,
})
txt, bad = CL._reconcile_line("G2", p)
check("R6b source_unavailable 被渲染成「数据源不可用」", "数据源不可用" in txt)
check("R6b 判异常", bad is True)
check("R6b 不再走 empty_all 的含糊措辞", "三者全空" not in txt)

# --- R6c source_unavailable=False（文件在、真空仓）⇒ 不应报「数据源不可用」---
p = write("g2vac_%s.json" % D, {
    "date": D, "fills": [], "pending": [], "orphans": [], "ledger": {},
    "account_positions": {}, "issues": [], "fills_total": 0,
    "snapshot_missing": False, "source_unavailable": False,
})
txt, bad = CL._reconcile_line("G2", p)
check("R6c 真空仓不误报「数据源不可用」", "数据源不可用" not in txt)

# --- R7 snapshot_missing=True ⇒ 也必须标异常 ---
p = write("g2snap_%s.json" % D, {
    "date": D, "fills": [], "pending": [], "orphans": [], "ledger": {"603529.SH": 800},
    "account_positions": {}, "issues": [], "fills_total": 0, "snapshot_missing": True,
})
txt, bad = CL._reconcile_line("G2", p)
check("R7 快照缺失判异常", bad is True and "快照缺失" in txt)

# --- R8 正常对账（有数据、有 issues）⇒ 异常；无 issues ⇒ 正常 ---
p = write("g2ok_%s.json" % D, {
    "date": D, "fills": [{"code": "603529.SH"}], "ledger": {"603529.SH": 800},
    "account_positions": {"603529.SH": 800}, "pending": [], "orphans": [],
    "issues": [], "fills_total": 1, "snapshot_missing": False,
})
txt, bad = CL._reconcile_line("G2", p)
check("R8 有真实数据且无 issues ⇒ 正常", bad is False and "成交 1" in txt)

p = write("g2iss_%s.json" % D, {
    "date": D, "fills": [{"code": "x"}], "ledger": {"x": 1},
    "account_positions": {"x": 0}, "pending": [], "orphans": [{"code": "x"}],
    "issues": ["持仓差额: x"], "fills_total": 1, "snapshot_missing": False,
})
txt, bad = CL._reconcile_line("G2", p)
check("R8 有 issues ⇒ 异常", bad is True)

# --- R9 dict 形态 connected:false（单策略返回）---
p = write("single_%s.json" % D, {"connected": False, "error": "connect rc=-1"})
txt, bad = CL._reconcile_line("X", p)
check("R9 dict 形态连接失败也判异常", bad is True and "连接失败" in txt)

# --- R10 reconcile_g2.py 的 snapshot_missing 语义 ---
src = io.open(os.path.join(HERE, "reconcile_g2.py"), encoding="utf-8").read()
check("R10 snapshot_missing 单判据 = not _has_pos",
      "snapshot_missing = not _has_pos" in src)
check("R10 不再有 not acct 判据（CC §4 的双判据已消除）",
      "snapshot_missing = not acct" not in src)
check("R10 分支也用同一个局部变量（不是 or (not _has_pos)）",
      "if snapshot_missing or (not _has_pos):" not in src)
check("R10 STATE_DIR 统一走 _state_dir()（CC §5）",
      "def _state_dir()" in src and "os.path.join(_state_dir(), \"positions_" in src)
check("R10 新增 source_unavailable 标记", "source_unavailable" in src)
check("R10 真空仓有独立标记 EMPTY_POSITIONS", "EMPTY_POSITIONS" in src)
# ★ 消费方：source_unavailable 必须被渲染层真正读取（不是只写不读的死字段）
close_src = io.open(os.path.join(HERE, "p16_close_report.py"), encoding="utf-8").read()
check("R10 source_unavailable 被渲染层消费",
      'd.get("source_unavailable")' in close_src)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
