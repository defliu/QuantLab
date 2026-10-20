# -*- coding: utf-8 -*-
"""T-20261018-003 工程测试：G4 观察期回滚检查（rollback_check_g2.py）加固。

背景（2026-10-18）：G2 live 于 2026-09-15 换版到 0915_1522t 并写入 10 交易日观察期 trial，
但 `rollback_check_g2.py --auto` 每天只打印「样本不足（<3 笔），暂不判定」→ 外层
run_scheduled 统一打印「OK」→ 0915 的观察期回滚保护实际空转。根因链：16:45 纸面管道
`paper_forward_daily.ps1` 把解释器硬编码为已损坏的 TRAE SOLO 内置 python（T-20261012-001
漏修的第 4 个脚本）→ 每天 BUILD-ALERT 退出 → `paper_forward.py --backfill` 停摆 →
`paper_forward_live.csv` 的 ret 停在 09-16（09-17 起其实可算的候选一笔都没回填）。
（解释器修复在 ps1 侧，由 BOM/解析校验覆盖；本测试覆盖 Python 侧加固。）

用例：
  R1 无 trial            → exit 0 + VERDICT=no_trial
  R2 观察期未结束         → exit 0 + VERDICT=in_window
  R3 主源样本足且不劣     → exit 0 + VERDICT=pass（trial 被清除、note 追加）
  R4 主源不足+次级源可用   → 走次级源判定（不再卡"样本不足"），rank>3 行被排除
  R5 两源均不足+已超宽限   → exit 3 + VERDICT=insufficient_alarm（供调度告警）
  R6 两源均不足+仍在宽限   → exit 0 + VERDICT=insufficient_grace
  R7 深度劣化+--auto+连续3日 → 自动回退到 prev_model + VERDICT=auto_rollback
  R8 轻度劣化+未 --apply   → exit 2 + VERDICT=suggest_rollback（不写指针）

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_rollback_check_g2.py
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

import rollback_check_g2 as RC  # noqa: E402

PASS = 0
FAIL = 0


def _check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


def _patch(tmp, notify_sink):
    """把模块级路径常量指向沙箱，并停掉飞书。"""
    RC.POINTER = os.path.join(tmp, "g2_live_model.json")
    RC.LIVE_CSV = os.path.join(tmp, "paper_forward_live.csv")
    RC.EXIT_CSV = os.path.join(tmp, "paper_forward_exit_live.csv")
    RC.STREAK_FILE = os.path.join(tmp, "rollback_streak.json")
    RC._notify = lambda text: notify_sink.append(text) or True


def _write_pointer(tmp, promoted_at, prev_model, prev_top2_ret=0.00953, ends_after_days=10):
    p = {
        "model_path": os.path.join(tmp, "cand_model.txt"),
        "meta_path": os.path.join(tmp, "cand_meta.json"),
        "promoted_at": promoted_at,
        "trial": {"ends_after_days": ends_after_days, "prev_model": prev_model,
                  "prev_ic": 0.12684, "prev_top2_ret": prev_top2_ret, "note": "观察期"},
        "note": "test",
    }
    with open(os.path.join(tmp, "g2_live_model.json"), "w", encoding="utf-8") as f:
        json.dump(p, f, ensure_ascii=False)
    return p


def _write_live_csv(tmp, dates, ret_by_date, hold=10):
    """dates: 候选日列表；ret_by_date: {日期: [ret...]}（写 rank 1..n）。"""
    rows = []
    for d in dates:
        rs = ret_by_date.get(d, [None] * 3)
        for i, r in enumerate(rs, start=1):
            rows.append({"date": d, "code": "60000%d.SH" % i, "total_new": 70.0 - i,
                         "prob": 0.7, "entry": 10.0, "exit": 11.0, "ret": r, "hold": hold, "rank": i})
    pd.DataFrame(rows).to_csv(os.path.join(tmp, "paper_forward_live.csv"), index=False, encoding="utf-8-sig")


def _write_exit_csv(tmp, entries):
    """entries: [(entry_day, rank, live_trail_ret)]"""
    rows = [{"date": e[0], "code": "600001.SH", "rank": e[1], "entry_day": e[0], "entry": 10.0,
             "none_ret": e[2], "live_trail_ret": e[2]} for e in entries]
    pd.DataFrame(rows).to_csv(os.path.join(tmp, "paper_forward_exit_live.csv"), index=False, encoding="utf-8-sig")


def _run():
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = RC.main()
    return rc, buf.getvalue()


def main():
    # 观察期内需 >=10 个交易日；用 12 个候选日制造 ndays=12
    dts = ["2026-09-%02d" % d for d in range(15, 27)]          # 12 个"交易日"（近似，够 ndays 计算）

    # ---- R1 无 trial ----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        with open(RC.POINTER, "w", encoding="utf-8") as f:
            json.dump({"model_path": "m.txt", "note": "x"}, f, ensure_ascii=False)
        rc, out = _run()
        _check("R1 无 trial → exit 0", rc == 0, "rc=%s" % rc)
        _check("R1 VERDICT=no_trial", "VERDICT=no_trial" in out, out[-120:])

    # ---- R2 观察期未结束（promoted 才 2 天）----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        _write_pointer(tmp, "2026-09-25 00:38:37", os.path.join(tmp, "prev.txt"))
        _write_live_csv(tmp, dts, {})
        rc, out = _run()
        _check("R2 观察期未结束 → exit 0", rc == 0, "rc=%s" % rc)
        _check("R2 VERDICT=in_window", "VERDICT=in_window" in out, out[-160:])

    # ---- R3 主源样本足且不劣 → pass（trial 清除）----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        _write_pointer(tmp, "2026-09-15 00:38:37", os.path.join(tmp, "prev.txt"))
        rbd = {d: [0.03, 0.02, None] for d in dts}      # 均值 +2.5% >> 基线 +0.95%
        _write_live_csv(tmp, dts, rbd)
        rc, out = _run()
        ptr = json.load(open(RC.POINTER, encoding="utf-8"))
        _check("R3 不劣 → exit 0 + pass", rc == 0 and "VERDICT=pass" in out, "rc=%s" % rc)
        _check("R3 trial 已清除", "trial" not in ptr, str(ptr.get("trial"))[:60])
        _check("R3 note 追加观察期通过", "观察期通过" in ptr.get("note", ""), ptr.get("note", "")[:80])

    # ---- R4 主源不足（模拟本次事故：ret 只到前 2 天）+ 次级源可用 ----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        _write_pointer(tmp, "2026-09-15 00:38:37", os.path.join(tmp, "prev.txt"))
        rbd = {d: [None, None, None] for d in dts}
        rbd[dts[0]] = [0.05, 0.04, None]                # 主源仅 2 笔（<3）
        _write_live_csv(tmp, dts, rbd)
        _write_exit_csv(tmp, [("2026-09-16", 1, -0.093), ("2026-09-17", 2, -0.090),
                              ("2026-09-18", 3, -0.099), ("2026-09-18", 4, 9.99)])  # rank4 应被排除
        rc, out = _run()
        _check("R4 主源不足改用次级源", "改用次级源" in out, out[-400:])
        _check("R4 次级源 n=3（rank>3 被排除）", "n=3" in out, out[-400:])
        _check("R4 判为深度劣化（-9.4% << +0.95%）", "VERDICT=deep_worse" in out, out[-200:])

    # ---- R5 两源均不足 + 超宽限期（ndays=12 >= 10+10? 否 → 用 ends=1 制造超期）----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        _write_pointer(tmp, "2026-09-15 00:38:37", os.path.join(tmp, "prev.txt"), ends_after_days=1)
        _write_live_csv(tmp, dts, {})
        rc, out = _run()
        _check("R5 超宽限仍无样本 → exit 3", rc == RC.EXIT_ALARM, "rc=%s" % rc)
        _check("R5 VERDICT=insufficient_alarm", "VERDICT=insufficient_alarm" in out, out[-300:])
        _check("R5 打出 ALERT 提示", "[ALERT]" in out, out[-300:])

    # ---- R6 两源均不足 + 仍在宽限期 ----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        _write_pointer(tmp, "2026-09-15 00:38:37", os.path.join(tmp, "prev.txt"), ends_after_days=10)
        _write_live_csv(tmp, dts, {})
        rc, out = _run()
        _check("R6 宽限期内 → exit 0", rc == 0, "rc=%s" % rc)
        _check("R6 VERDICT=insufficient_grace", "VERDICT=insufficient_grace" in out, out[-250:])

    # ---- R7 深度劣化 + --auto + 连续 3 日 → 自动回退 ----
    with tempfile.TemporaryDirectory() as tmp:
        sink = []
        _patch(tmp, sink)
        prev_model = os.path.join(tmp, "prev_model.txt")
        with open(prev_model, "w", encoding="utf-8") as f:
            f.write("stub")
        _write_pointer(tmp, "2026-09-15 00:38:37", prev_model)
        _write_live_csv(tmp, dts, {d: [-0.09, -0.08, None] for d in dts})
        old_argv = sys.argv
        try:
            sys.argv = ["rollback_check_g2.py", "--auto"]
            rc, out = _run()          # 第 1 日
            if "VERDICT=deep_worse" in out:
                rc, out = _run()      # 第 2 日
            if "VERDICT=deep_worse" in out:
                rc, out = _run()      # 第 3 日 → 触发自动回退
        finally:
            sys.argv = old_argv
        ptr = json.load(open(RC.POINTER, encoding="utf-8"))
        _check("R7 连续 3 日 → 自动回退", rc == 0 and "VERDICT=auto_rollback" in out, "rc=%s out=%s" % (rc, out[-160:]))
        _check("R7 指针已指向 prev_model", ptr["model_path"] == prev_model, ptr["model_path"])
        _check("R7 trial 已清除 + note 记回退", "trial" not in ptr and "G4 观察期回退" in ptr.get("note", ""),
               ptr.get("note", "")[:100])
        _check("R7 已推送飞书告警", any("自动回退" in t for t in sink), str(sink)[:120])

    # ---- R8 轻度劣化（-0.01% 级）+ 未 --apply → suggest_rollback, 不写指针 ----
    with tempfile.TemporaryDirectory() as tmp:
        _patch(tmp, [])
        _write_pointer(tmp, "2026-09-15 00:38:37", os.path.join(tmp, "prev.txt"))
        # 均值 +0.90% vs 基线 +0.953% → 轻度劣化（差 0.05pp 以内，不触发 deep）
        _write_live_csv(tmp, dts, {d: [0.0090, 0.0090, None] for d in dts})
        rc, out = _run()
        ptr = json.load(open(RC.POINTER, encoding="utf-8"))
        _check("R8 轻度劣化 → exit 2", rc == 2, "rc=%s | %s" % (rc, out[-160:]))
        _check("R8 VERDICT=suggest_rollback", "VERDICT=suggest_rollback" in out, out[-160:])
        _check("R8 未 --apply 时不写指针（trial 保留）", "trial" in ptr, str(ptr.get("trial"))[:60])

    print("\n结果: PASS=%d FAIL=%d" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
