# -*- coding: utf-8 -*-
"""卖出风控优先级回归测试（T-20261002-001）。

背景（2026-09-29 实盘事故）：rebalance_daily.py 在「当日无候选通过红线」时于买入侧
直接 `return`，而该 return 位于**持仓读取 / 成熟度计算 / sell_plan 生成之前** →
无候选达标时到期卖出分支根本走不到。实测 09-29：10 只候选全未过 58 红线 →
603049.SH（超期 9 日）、603237.SH（超期 8 日）本应按 MATURE 卖出却零委托，
持仓被无限期滞留，直接违反 AGENTS.md 硬红线「卖出风控优先级高于买入优化」。

修复语义：买入拦截只置 buy_allowed=False，卖出通道（sell_plan / trim_orders）
完全不动，仅把 exec_buys 压到 0。

测试策略：mock 掉 QMT / 行情 / 候选清单等外部依赖，真实驱动 main() 走 dry-run 分支
（dry-run 在 `import order_guard` 与 `_connect()` 之前 return，故不连真 QMT、不产生真实委托），
断言产出的旁路 dryrun.json。

用例：
  S1 事故复现  ：红线空仓 + 持仓已满 HOLD_DAYS → sell_plan 必须含 MATURE 卖出（修复前为空）
  S2 买入抑制  ：同一场景下 buy 必须为空（买入拦截仍生效，闸门未被误删）
  S3 未到期保留：红线空仓 + 持仓未满 HOLD_DAYS → 不卖，保留（到期制语义未被破坏）
  S4 T=0 不回归 ：T=0 清仓信号 → 全部持仓 T0_LIQUIDATE（2026-09-04 修复未被本次改动破坏）
  S5 源码断言  ：买入拦截分支内不得残留裸 return（防止未来重构把短路改回来）
  S6 目标票保留 ：仍在 target 内的持仓即便红线空仓也不被动（target 空时该分支不适用，
                 故用「部分达标」场景验证 hold 语义不受 buy_allowed 影响）
  S7 清单缺失卖出（T-20261003-001 P1）：清单文件不存在 + 持仓到期 → MATURE 卖出照常、
                 买入为空（修复前 main 直接 return，无 plan 产出）
  S8 清单读失败卖出（同上）：清单路径存在但读取抛异常 → 卖出照常（修复前 return）
  S9 清单缺失+T=0（同上）：清仓信号不因清单缺失失效，全部 T0_LIQUIDATE
  S10 源码断言（同上）：清单缺失/读取失败分支内不得残留裸 return（防回归绊线）

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_rebalance_sell_priority.py
"""
import os
import sys
import types
import json
import shutil
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# ---- 屏蔽真 xtquant：只提供 rebalance_daily 顶层 import 链所需的最小 stub ----
# 位置过滤 position_filter 会 import xtquant.xtdata；此处不提供该子模块，
# 使取价走 except 分支返回 None 之外的行为由下方 mock 覆盖。
_xtquant = types.ModuleType("xtquant")
_xtconstant = types.ModuleType("xtquant.xtconstant")
_xtconstant.STOCK_BUY = 23
_xtconstant.STOCK_SELL = 24
_xtconstant.FIX_PRICE = 11
_xtconstant.LATEST_PRICE = 5
_xtquant.xtconstant = _xtconstant
sys.modules.setdefault("xtquant", _xtquant)
sys.modules.setdefault("xtquant.xtconstant", _xtconstant)

import rebalance_daily as R  # noqa: E402

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


def _write_selection(dirpath, date_str, rows):
    """写当日完整版清单；rows=[(code,total)]，total 低于 REDLINE 即被过滤。"""
    sel_dir = os.path.join(dirpath, "selections")
    if not os.path.isdir(sel_dir):
        os.makedirs(sel_dir)
    path = os.path.join(sel_dir, "%s_selection_full.csv" % date_str)
    # 列名必须与生产 load_selection 的读取口径一致：ts_code / total / model_prob
    with open(path, "w", encoding="utf-8") as f:
        f.write("ts_code,name,total,model_prob\n")
        for code, total in rows:
            f.write("%s,TEST,%.1f,0.5\n" % (code, total))
    return path


def _run_dryrun(tmpdir, date_str, sel_rows, positions, sellable, last_buy, args_top=2,
                sel_mode="normal"):
    """mock 外部依赖后真实驱动 main() 走 dry-run，返回产出的 plan dict。

    sel_mode: normal=写正常清单 / missing=不写清单（os.path.exists=False 分支）/
              broken=在清单路径建目录（exists=True 但 open() 失败 → read_error 分支）
    """
    if sel_mode == "normal":
        sel_path = _write_selection(tmpdir, date_str, sel_rows)
    else:
        sel_dir = os.path.join(tmpdir, "selections")
        if not os.path.isdir(sel_dir):
            os.makedirs(sel_dir)
        sel_path = os.path.join(sel_dir, "%s_selection_full.csv" % date_str)
        if sel_mode == "broken":
            os.makedirs(sel_path)   # 目录占位：Windows 下 open() 抛 PermissionError

    orig = {
        "TRADE_LOG": R.C.TRADE_LOG,
        "CAP_FILE": R.CAP_FILE,
        "load_positions_qmt": R.load_positions_qmt,
        "load_strategy_capital": R.load_strategy_capital,
        "_build_last_buy_dates": R._build_last_buy_dates,
        "_fetch_price": R._fetch_price,
        "load_trade_log_rows": R.C.load_trade_log_rows,
    }
    try:
        # 产出隔离到 tmpdir，绝不污染真实 data/ 目录
        R.C.TRADE_LOG = os.path.join(tmpdir, "qmt_trade_log.csv")
        R.CAP_FILE = os.path.join(tmpdir, "strategy_capital.json")
        R.C.load_trade_log_rows = lambda: [
            {"side": "BUY", "code": c, "time": d} for c, d in last_buy.items()
        ]
        R.load_positions_qmt = lambda: (dict(positions), dict(positions), dict(sellable))
        R.load_strategy_capital = lambda: 100000.0
        # 真实 _build_last_buy_dates 返回 {code: date}，此处保持同一契约
        R._build_last_buy_dates = lambda: {
            c: datetime.strptime(d, "%Y-%m-%d").date() for c, d in last_buy.items()
        }
        R._fetch_price = lambda code: 10.0

        argv = sys.argv
        sys.argv = ["rebalance_daily.py", "--date", date_str, "--top", str(args_top)]
        try:
            R.main()
        finally:
            sys.argv = argv
    finally:
        R.C.TRADE_LOG = orig["TRADE_LOG"]
        R.CAP_FILE = orig["CAP_FILE"]
        R.load_positions_qmt = orig["load_positions_qmt"]
        R.load_strategy_capital = orig["load_strategy_capital"]
        R._build_last_buy_dates = orig["_build_last_buy_dates"]
        R._fetch_price = orig["_fetch_price"]
        R.C.load_trade_log_rows = orig["load_trade_log_rows"]

    out = os.path.join(tmpdir, "rebalance_%s.dryrun.json" % date_str)
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def test_s1_incident_mature_sell_survives_redline_block():
    """S1 事故复现：红线空仓 + 持仓已到期 → sell_plan 必须含 MATURE（修复前为空）。"""
    tmp = tempfile.mkdtemp(prefix="t20260929_s1_")
    try:
        # 10 只候选全部低于红线 58 → 触发买入拦截
        sel = [("%06d.SZ" % (300000 + i), 50.0 + i * 0.5) for i in range(10)]
        # 两只票均已持有 9 个日历日跨度（远超 HOLD_DAYS=5 交易日）
        plan = _run_dryrun(
            tmp, "20260929", sel,
            positions={"603049.SH": 44.0, "603237.SH": 14.0},
            sellable={"603049.SH": 900, "603237.SH": 2800},
            last_buy={"603049.SH": "2026-09-16", "603237.SH": "2026-09-17"},
        )
        codes = [s["code"] for s in plan["sell"]]
        _check("S1 到期票 603049.SH 进入卖出清单（修复前被短路）", "603049.SH" in codes, codes)
        _check("S1 到期票 603237.SH 进入卖出清单（修复前被短路）", "603237.SH" in codes, codes)
        reasons = {s["code"]: s.get("reason") for s in plan["sell"]}
        _check("S1 603049.SH 原因=MATURE", reasons.get("603049.SH") == "MATURE", reasons)
        _check("S1 603237.SH 原因=MATURE", reasons.get("603237.SH") == "MATURE", reasons)
        vols = {s["code"]: s["vol"] for s in plan["sell"]}
        _check("S1 603049.SH 卖出量=900（用可卖量）", vols.get("603049.SH") == 900, vols)
        _check("S1 603237.SH 卖出量=2800（用可卖量）", vols.get("603237.SH") == 2800, vols)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s2_buy_still_blocked():
    """S2 买入抑制：红线空仓时买入必须为 0（闸门有效，未被误删）。"""
    tmp = tempfile.mkdtemp(prefix="t20260929_s2_")
    try:
        sel = [("%06d.SZ" % (300000 + i), 50.0 + i * 0.5) for i in range(10)]
        plan = _run_dryrun(
            tmp, "20260929", sel,
            positions={"603049.SH": 44.0},
            sellable={"603049.SH": 900},
            last_buy={"603049.SH": "2026-09-16"},
        )
        _check("S2 买入为空（红线空仓不买）", plan["buy"] == [], plan["buy"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s3_unmatured_position_kept():
    """S3 到期制语义未被破坏：未满 HOLD_DAYS 的持仓仍保留，不误卖。"""
    tmp = tempfile.mkdtemp(prefix="t20260929_s3_")
    try:
        sel = [("%06d.SZ" % (300000 + i), 50.0 + i * 0.5) for i in range(10)]
        # 仅 1 个交易日持有 < HOLD_DAYS(5) → 应保留
        plan = _run_dryrun(
            tmp, "20260929", sel,
            positions={"600547.SH": 26.0},
            sellable={"600547.SH": 600},
            last_buy={"600547.SH": "2026-09-28"},
        )
        codes = [s["code"] for s in plan["sell"]]
        _check("S3 未到期持仓不进卖出清单", "600547.SH" not in codes, codes)
        _check("S3 未到期持仓进入 hold", "600547.SH" in plan["hold"], plan["hold"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s4_t0_liquidate_not_regressed():
    """S4 T=0 清仓信号未被本次改动破坏：全部持仓 T0_LIQUIDATE。"""
    tmp = tempfile.mkdtemp(prefix="t20260929_s4_")
    try:
        sel = [("%06d.SZ" % (300000 + i), 50.0 + i * 0.5) for i in range(10)]
        plan = _run_dryrun(
            tmp, "20260929", sel,
            positions={"603049.SH": 44.0, "600547.SH": 26.0},
            sellable={"603049.SH": 900, "600547.SH": 600},
            last_buy={"603049.SH": "2026-09-28", "600547.SH": "2026-09-28"},
            args_top=0,
        )
        codes = [s["code"] for s in plan["sell"]]
        reasons = {s["code"]: s.get("reason") for s in plan["sell"]}
        _check("S4 T=0 两只票全部卖出", set(codes) == {"603049.SH", "600547.SH"}, codes)
        _check("S4 T=0 原因=T0_LIQUIDATE（未到期也清）",
               all(v == "T0_LIQUIDATE" for v in reasons.values()), reasons)
        _check("S4 T=0 不买入", plan["buy"] == [], plan["buy"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s5_no_bare_return_in_buy_block():
    """S5 源码断言：买入拦截分支内不得残留裸 return（防未来重构改回短路）。"""
    path = os.path.join(PROJECT_ROOT, "rebalance_daily.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines()
    # 定位 `if not target:` 块，确认其 else 分支只置 buy_allowed=False 而非 return
    start = None
    for i, ln in enumerate(lines):
        if ln.strip() == "if not target:":
            start = i
            break
    _check("S5 定位到 `if not target:` 块", start is not None)
    if start is None:
        return
    block = []
    for ln in lines[start:start + 20]:
        stripped = ln.strip()
        if stripped.startswith("buy_allowed = False"):
            block.append(ln)
        # 买入拦截语境下的裸 return 即回归信号
        if stripped == "return" and "买入" not in stripped:
            _check("S5 买入拦截分支无裸 return", False, ln)
    _check("S5 买入拦截分支置 buy_allowed=False", any("buy_allowed = False" in b for b in block))
    # 卖出风控必须先于买入闸门存在：sell_plan 生成行号 < 买入闸门行号
    sell_idx = next((i for i, ln in enumerate(lines) if "sell_plan = []" in ln), None)
    gate_idx = next((i for i, ln in enumerate(lines) if "if not buy_allowed:" in ln), None)
    _check("S5 sell_plan 生成早于买入闸门", sell_idx is not None and gate_idx is not None
           and sell_idx < gate_idx, "sell@%s gate@%s" % (sell_idx, gate_idx))


def test_s6_target_position_not_sold_when_buy_allowed():
    """S6 部分达标场景：target 内持仓保留、target 外到期票卖出，买卖互不干扰。"""
    tmp = tempfile.mkdtemp(prefix="t20260929_s6_")
    try:
        # 3 只过红线 → target 非空 → 正常买入通道 + 卖出通道同时工作
        sel = [("000920.SZ", 63.0), ("603882.SH", 63.0), ("600547.SH", 62.5)]
        plan = _run_dryrun(
            tmp, "20260930", sel,
            positions={"600547.SH": 26.0, "603049.SH": 44.0},
            sellable={"600547.SH": 600, "603049.SH": 900},
            last_buy={"600547.SH": "2026-09-16", "603049.SH": "2026-09-16"},
        )
        sell_codes = [s["code"] for s in plan["sell"]]
        _check("S6 target 内 600547.SH 不卖", "600547.SH" not in sell_codes, sell_codes)
        _check("S6 target 外到期 603049.SH 卖", "603049.SH" in sell_codes, sell_codes)
        _check("S6 正常场景买入未被误清（应产生买单）", len(plan["buy"]) > 0, plan["buy"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s7_missing_selection_mature_sell_survives():
    """S7 清单缺失：到期卖出照常执行（修复前 main 直接 return，无 plan 产出）。"""
    tmp = tempfile.mkdtemp(prefix="t20261003_s7_")
    try:
        plan = _run_dryrun(
            tmp, "20260929", [],
            positions={"603049.SH": 44.0, "603237.SH": 14.0},
            sellable={"603049.SH": 900, "603237.SH": 2800},
            last_buy={"603049.SH": "2026-09-16", "603237.SH": "2026-09-17"},
            sel_mode="missing",
        )
        codes = [s["code"] for s in plan["sell"]]
        reasons = {s["code"]: s.get("reason") for s in plan["sell"]}
        _check("S7 清单缺失：两只到期票均进卖出清单（修复前 main 直接 return）",
               set(codes) == {"603049.SH", "603237.SH"}, codes)
        _check("S7 原因=MATURE", all(v == "MATURE" for v in reasons.values()), reasons)
        _check("S7 买入为空（无清单不买）", plan["buy"] == [], plan["buy"])
        _check("S7 candidate_loader=none 留痕", plan["candidate_loader"] == "none",
               plan.get("candidate_loader"))
        _check("S7 candidate_rows=0（过滤后候选数口径完整，防 KeyError）",
               plan["candidate_rows"] == 0, plan.get("candidate_rows"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s8_broken_selection_mature_sell_survives():
    """S8 清单读取失败：到期卖出照常执行（修复前 main 直接 return）。"""
    tmp = tempfile.mkdtemp(prefix="t20261003_s8_")
    try:
        plan = _run_dryrun(
            tmp, "20260929", [],
            positions={"603049.SH": 44.0},
            sellable={"603049.SH": 900},
            last_buy={"603049.SH": "2026-09-16"},
            sel_mode="broken",
        )
        codes = [s["code"] for s in plan["sell"]]
        _check("S8 清单读取失败：到期票 603049.SH 仍进卖出清单", "603049.SH" in codes, codes)
        _check("S8 买入为空", plan["buy"] == [], plan["buy"])
        _check("S8 candidate_loader=read_error 留痕", plan["candidate_loader"] == "read_error",
               plan.get("candidate_loader"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s9_missing_selection_t0_liquidate_survives():
    """S9 清单缺失 + T=0：清仓信号不因清单缺失失效，全部 T0_LIQUIDATE。"""
    tmp = tempfile.mkdtemp(prefix="t20261003_s9_")
    try:
        plan = _run_dryrun(
            tmp, "20260929", [],
            positions={"603049.SH": 44.0, "600547.SH": 26.0},
            sellable={"603049.SH": 900, "600547.SH": 600},
            last_buy={"603049.SH": "2026-09-28", "600547.SH": "2026-09-28"},
            args_top=0,
            sel_mode="missing",
        )
        codes = [s["code"] for s in plan["sell"]]
        reasons = {s["code"]: s.get("reason") for s in plan["sell"]}
        _check("S9 清单缺失+T=0：两只票全部卖出", set(codes) == {"603049.SH", "600547.SH"}, codes)
        _check("S9 原因=T0_LIQUIDATE（未到期也清）",
               all(v == "T0_LIQUIDATE" for v in reasons.values()), reasons)
        _check("S9 不买入", plan["buy"] == [], plan["buy"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_s10_no_bare_return_in_selection_missing_block():
    """S10 源码断言：清单缺失/读取失败分支不得残留裸 return（防回归绊线）。"""
    path = os.path.join(PROJECT_ROOT, "rebalance_daily.py")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip() == "if not os.path.exists(sel_path):"), None)
    _check("S10 定位到清单存在性检查行", start is not None)
    if start is None:
        return
    end = next((i for i, ln in enumerate(lines)
                if "target = load_selection" in ln), None)
    _check("S10 定位到 target 装载行", end is not None and end > start,
           "start=%s end=%s" % (start, end))
    if end is None:
        return
    seg = [ln.strip() for ln in lines[start:end]]
    _check("S10 清单缺失/读取失败分支无裸 return（修复前此处有 2 个）",
           not any(ln == "return" for ln in seg), [ln for ln in seg if "return" in ln])


if __name__ == "__main__":
    print("== 卖出风控优先级回归测试（T-20261002-001 + T-20261003-001）==")
    test_s1_incident_mature_sell_survives_redline_block()
    test_s2_buy_still_blocked()
    test_s3_unmatured_position_kept()
    test_s4_t0_liquidate_not_regressed()
    test_s5_no_bare_return_in_buy_block()
    test_s6_target_position_not_sold_when_buy_allowed()
    test_s7_missing_selection_mature_sell_survives()
    test_s8_broken_selection_mature_sell_survives()
    test_s9_missing_selection_t0_liquidate_survives()
    test_s10_no_bare_return_in_selection_missing_block()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
