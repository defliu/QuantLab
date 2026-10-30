# coding: utf-8
"""G2 计划落盘前时间门回归（2026-10-30 立，CC 复核 §3 补）。

钉住的事实：19:22 那次事故里两个被覆盖的计划文件，**可恢复性完全不同**——
  · V13：688B/3 腿 → 313B/0 腿，**可恢复**（HEAD 8a95da2）
  · G2 ：2 腿 BUY → 1 腿 SELL，**不可恢复**（未入 git）
而 `Quant_P16_G2_PlanOnly_0940` 是**直接调 `rebalance_g2.py --plan-only`**、中间没有 wrapper，
`StartWhenAvailable=true` ⇒ 下一次补跑会一模一样再覆盖一次。

本轮把门加在 `rebalance_g2.py` 的**落盘之前**（`main()` 里、`build_plan` 之前）：
过 `PLAN_ONLY_DEADLINE_HM` 就 return 4，不 build、不落盘。

纯离线：用子进程跑真脚本 + 临时输出目录，不碰真计划文件、不连 xtquant。
"""
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


src_path = os.path.join(HERE, "rebalance_g2.py")
src = io.open(src_path, encoding="utf-8").read()

# --- G1 常量存在且早于执行壳窗口 ---
spec = importlib.util.spec_from_file_location("rg2", src_path)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)

check("G1 有 PLAN_ONLY_DEADLINE_HM 常量", hasattr(R, "PLAN_ONLY_DEADLINE_HM"))
check("G1 截止时刻 = 958（= 执行壳窗口终点）", R.PLAN_ONLY_DEADLINE_HM == 958)
check("G1 有 PLAN_ONLY_FROZEN_HM 常量（窗口内不覆盖阈值）",
      hasattr(R, "PLAN_ONLY_FROZEN_HM"))
check("G1 冻结阈值 = 950（= 执行壳窗口起点）", R.PLAN_ONLY_FROZEN_HM == 950)
check("G1 两个阈值不相等（终点 vs 起点，别合并）",
      R.PLAN_ONLY_DEADLINE_HM != R.PLAN_ONLY_FROZEN_HM)

# 与执行壳产物的窗口常量对齐（防两处漂移）
prod = os.path.join(HERE, "build", "strategy_p16_g2_exec.py")
if os.path.exists(prod):
    import re
    txt = io.open(prod, encoding="gbk").read()
    m_end = re.search(r"^EXEC_HM_END = (\d+)", txt, re.M)
    check("G1 截止时刻与执行壳 EXEC_HM_END 一致",
          m_end and int(m_end.group(1)) == R.PLAN_ONLY_DEADLINE_HM)
    m_start = re.search(r"^EXEC_HM_START = (\d+)", txt, re.M)
    check("G1 冻结阈值与执行壳 EXEC_HM_START 一致",
          m_start and int(m_start.group(1)) == R.PLAN_ONLY_FROZEN_HM)

# --- G2 门的位置：必须在 build_plan / _save_plan 之前 ---
i_gate = src.find("if args.plan_only and not args.date:")
i_build = src.find("orders, plan = build_plan(date, capital)")
i_save = src.find("def _save_plan(plan, date):")
check("G2 门在 build_plan 之前", i_gate != -1 and i_build != -1 and i_gate < i_build)
check("G2 门在 _save_plan 定义之前（即调用点之前）", i_gate != -1 and i_gate < i_save)

# --- G3 门只在 --plan-only 生效（--live 不受影响）---
check("G3 门条件含 plan_only", "args.plan_only and not args.date" in src)

import argparse


def run_main(argv, fake_hhmm, tmpdir):
    """跑 R.main()，返回 (rc, 产生的 plan 文件列表)。"""
    old_argv = sys.argv
    old_data = R.G.DATA_DIR
    old_cap = R.G.load_g2_capital
    old_strf = R.time.strftime
    old_notify = R.notify_feishu      # ★ 必须桩掉：否则 G5b 会真推一条飞书

    def _fake_strftime(fmt, *a):
        # ★ 只替换「无参」的 %H%M（即 main() 里取当前时刻那次）；
        #   带参的 time.strftime("%H%M", time.localtime(mtime)) 是读文件 mtime，
        #   必须走真实时钟，否则冻结判据永远看不到真实的 09:52。
        if fmt == "%H%M" and not a:
            return "%04d" % fake_hhmm
        return old_strf(fmt, *a)

    R.G.DATA_DIR = tmpdir
    R.G.load_g2_capital = lambda: 100000.0
    R.time.strftime = _fake_strftime
    R.notify_feishu = lambda *a, **k: None
    sys.argv = ["rebalance_g2.py"] + argv
    try:
        rc = R.main()
    except SystemExit as e:
        rc = e.code
    finally:
        R.G.DATA_DIR = old_data
        R.G.load_g2_capital = old_cap
        R.time.strftime = old_strf
        R.notify_feishu = old_notify
        sys.argv = old_argv
    got = []
    rd = os.path.join(tmpdir, "rebalance_g2")
    if os.path.isdir(rd):
        got = sorted(os.listdir(rd))
    return rc, got


# --- G4 边界真行为：949 落盘 / 950 不落盘（不是算术，是跑 main()）---
#    ★ CC 复核 §7.2：早期 G4 是 `949 < 950` / `950 >= 950` 两条纯算术 + 一条源码 grep，
#      跟被测代码无关。现在改成真行为边界，与 G5 共用同一套 run_main。
#    （run_main 定义在下面 G5 段，这里先定义以便复用）

# --- G5 驱动 main()：patch _hhmm 与 G.load_g2_capital，验证过门时不落盘 ---
# 直接改模块内的 time.strftime 太脆，这里改为：把 src 里的判定块单独抽出来执行语义验证
# —— 更可靠的做法是 monkeypatch R.time.strftime 并让 main() 走 args。
tmp = tempfile.mkdtemp()
try:
    # --- G4 边界真行为：957 落盘 / 958 不落盘（跑真 main()，不是算术）---
    #    ★ CC 复核 §7.2：早期 G4 是两条纯算术 + 一条源码 grep。现在改成真行为边界。
    #    ★ 2026-10-31 门改 958 后：窗口终点前 1 分钟（957）仍应落盘，窗口终点（958）拒。
    tmp4 = tempfile.mkdtemp()
    try:
        rc4a, f4a = run_main(["--plan-only"], 957, tmp4)
        check("G4a 0957（窗口终点前 1 分钟）=> 落盘（rc!=4 且产出文件）",
              rc4a != 4 and len(f4a) > 0)
    finally:
        shutil.rmtree(tmp4, ignore_errors=True)
    tmp4b = tempfile.mkdtemp()
    try:
        rc4b, f4b = run_main(["--plan-only"], 958, tmp4b)
        check("G4b 0958（正好到窗口终点）=> 不落盘（rc=4 且无文件）",
              rc4b == 4 and f4b == [])
    finally:
        shutil.rmtree(tmp4b, ignore_errors=True)

    # --- G6 窗口内已就位计划不得被覆盖（G6 是 §9-6 选 B 的配套前提）---
    tmp6 = tempfile.mkdtemp()
    try:
        d6 = os.path.join(tmp6, "rebalance_g2")
        os.makedirs(d6, exist_ok=True)
        p6 = os.path.join(d6, "rebalance_g2_%s.plan.json" % time.strftime("%Y%m%d"))
        with io.open(p6, "w", encoding="utf-8") as f:
            f.write(u'{"marker": "frozen"}')
        # 把 mtime 拨到「今天 09:52」= 窗口内
        _t = time.localtime()
        _ts = time.mktime((_t.tm_year, _t.tm_mon, _t.tm_mday, 9, 52, 0, 0, 0, -1))
        os.utime(p6, (_ts, _ts))
        rc6, f6 = run_main(["--plan-only"], 945, tmp6)
        _body = io.open(p6, encoding="utf-8").read()
        check("G6 窗口内已就位计划 => rc=5 且原文件未被覆盖",
              rc6 == 5 and "frozen" in _body)
    finally:
        shutil.rmtree(tmp6, ignore_errors=True)

    # --- G6b 反例：mtime 在窗口起点之前（如 09:40）⇒ 允许覆盖 ---
    #    ★ 没有这条，把冻结判据写成「文件存在就永不覆盖」也能让 G6 通过。
    #      实际口径是「窗口内写的才冻结」，09:40 正常落盘的仍可在 09:5x 被修正。
    tmp6b = tempfile.mkdtemp()
    try:
        d6b = os.path.join(tmp6b, "rebalance_g2")
        os.makedirs(d6b, exist_ok=True)
        p6b = os.path.join(d6b, "rebalance_g2_%s.plan.json" % time.strftime("%Y%m%d"))
        with io.open(p6b, "w", encoding="utf-8") as f:
            f.write(u'{"marker": "early"}')
        _t2 = time.localtime()
        _ts2 = time.mktime((_t2.tm_year, _t2.tm_mon, _t2.tm_mday, 9, 40, 0, 0, 0, -1))
        os.utime(p6b, (_ts2, _ts2))
        rc6b, _f6b = run_main(["--plan-only"], 945, tmp6b)
        check("G6b mtime 09:40（窗口前）=> 允许覆盖（rc!=5）", rc6b != 5)
    finally:
        shutil.rmtree(tmp6b, ignore_errors=True)

    # 5a 过窗口 + --plan-only ⇒ rc=4 且**一个文件都不产生**
    rc, files = run_main(["--plan-only"], 1000, tmp)
    check("G5a 过窗口 rc=4", rc == 4)
    check("G5a 过窗口不落盘（目录为空或不存在）", files == [])

    # 5b 窗口前 + --plan-only ⇒ 不返回 4（门未触发）
    rc2, files2 = run_main(["--plan-only"], 940, tmp)
    check("G5b 窗口前不返回 4（门未触发）", rc2 != 4)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
