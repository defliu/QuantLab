# -*- coding: utf-8 -*-
"""T-20261009-008（E-4）ENS 大盘门控测试：rebalance_ens 三档 T=0/T=1/T=2 + fail-safe。

背景（ens_alignment_design_20260913.md 差异二 / 优化方案 §8.4 E-4）：
ENS 是同账户三链（V1.x/G2/ENS）中唯一无大盘门控的——大盘急跌日 G2 停买、
V1.x 有门控，ENS 照常满额建仓（10-09 实证：V1.x tier=1 半仓档时 ENS 照买 688117）。

2026-10-10 落地：import 复用 rebalance_g2 的 load_hs300_pct/_calc_tier + g2_config
阈值（口径同源）；T=2 时 deploy_pct(0.98) ≡ 1-RESERVE_CASH_PCT(2%)，行为零变化。

  R1. T=0（-2.0%）停买：无任何 BUY/订单，plan.tier=0 / deploy_pct=0.0
  R2. T=1（-1.2%）半仓：deploy_pct=0.5，target_value=资金池×0.5/TOP_N
  R3. 边界：恰 -1.0% 判满仓（< 严格小于，DE 体检 P2-6 口径）
  R4. T=2 满仓：deploy_pct=0.98，target_value 与旧公式 capital*(1-2%)/TOP_N 逐分一致
  R5. fail-safe：沪深300 取数失败 → T=1 半仓（降速不裸奔）
  R6. plan 溯源：hs300_pct/tier/deploy_pct 三字段落 plan
  R7. T=1 下 kept 超配减仓同按半仓目标（G2 同口径，单一 target_value）
  R8. T=0 下等权减仓/建仓整段不执行（deploy_pct>0 进闸，对齐 G2）
  R9. 降级闸 × T=0 双闸共存：downgrade_frozen 与 tier=0 同时落 plan，零订单

跑法：miniqmt venv python research/tests/test_rebalance_ens_tier_gate.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJ)

import g2_ens_config as GCFG
import paper_forward_downgrade as PFD
import rebalance_ens as E
import rebalance_g2 as RBG

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PASS = [0]
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
        print("  [PASS] %s" % name)
    else:
        FAIL.append(name)
        print("  [FAIL] %s %s" % (name, extra))


DATE = "20261010"
CAP = 100000.0
TOP_N = GCFG.TOP_N
MIN_LOT = GCFG.MIN_ORDER_VOL
POOL = [{"code": "600001.SH", "total": 88.0}]

# ---- 桩替换（原函数留档，run_case 内统一还原）----
ORIG = {
    "load_ens_selection": E.load_ens_selection,
    "load_ens_ledger": E.load_ens_ledger,
    "load_hold_dates": E.load_hold_dates,
    "fetch_price": E.fetch_price,
    "read_heart": E.read_heart,
    "load_sellable": E.load_sellable,
    "_fetch_ohlc": E._fetch_ohlc,
    "_count_trade_days": E._count_trade_days,
    "pfd_is_frozen": PFD.is_frozen,
    "rbg_load_hs300": RBG.load_hs300_pct,
    "g2_hold_dates_file": GCFG.G2_HOLD_DATES_FILE,
    # T-20261012-001：build_plan 新增 PF.st_guard 调用（主库 parquet + 腾讯 HTTP），
    # 测试桩化为恒放行（ST 剔除逻辑由 test_st_exclusion.py 专项覆盖）。
    "pf_st_guard": E.PF.st_guard,
}


def run_case(hs300_env=None, hs300_none=False, frozen=False, ledger=None, hold_dates=None):
    """桩化外部依赖后跑 build_plan，返回 (orders, plan)。"""
    meta = E._candidate_meta(DATE, None, "none")
    E.load_ens_selection = lambda d, t: (list(POOL), "test pool", meta)
    E.load_ens_ledger = lambda d: dict(ledger or {})
    E.load_hold_dates = lambda: dict(hold_dates or {})
    E.fetch_price = lambda code: 10.0
    E.read_heart = lambda date=None: {}
    E.load_sellable = lambda d, c: 100000
    E._fetch_ohlc = lambda code: None
    E._count_trade_days = lambda s, e: (2, True)
    E.PF.st_guard = lambda code, name=None: (False, None)
    PFD.is_frozen = lambda s: frozen
    GCFG.G2_HOLD_DATES_FILE = os.path.join(tempfile.gettempdir(), "nonexistent_g2hd_test.json")
    saved_env = os.environ.get("G2_HS300_PCT")
    try:
        if hs300_none:
            RBG.load_hs300_pct = lambda: None
            os.environ.pop("G2_HS300_PCT", None)
        elif hs300_env is not None:
            os.environ["G2_HS300_PCT"] = str(hs300_env)
        else:
            os.environ.pop("G2_HS300_PCT", None)
        orders, plan = E.build_plan(DATE, CAP)
    finally:
        E.load_ens_selection = ORIG["load_ens_selection"]
        E.load_ens_ledger = ORIG["load_ens_ledger"]
        E.load_hold_dates = ORIG["load_hold_dates"]
        E.fetch_price = ORIG["fetch_price"]
        E.read_heart = ORIG["read_heart"]
        E.load_sellable = ORIG["load_sellable"]
        E._fetch_ohlc = ORIG["_fetch_ohlc"]
        E._count_trade_days = ORIG["_count_trade_days"]
        E.PF.st_guard = ORIG["pf_st_guard"]
        PFD.is_frozen = ORIG["pfd_is_frozen"]
        RBG.load_hs300_pct = ORIG["rbg_load_hs300"]
        GCFG.G2_HOLD_DATES_FILE = ORIG["g2_hold_dates_file"]
        if saved_env is None:
            os.environ.pop("G2_HS300_PCT", None)
        else:
            os.environ["G2_HS300_PCT"] = saved_env
    return orders, plan


def test_t0_stop_buy():
    print("[R1] T=0 停买（G2_HS300_PCT=-2.0）")
    orders, plan = run_case(hs300_env=-2.0)
    check("tier=0", plan.get("tier") == 0)
    check("deploy_pct=0.0", plan.get("deploy_pct") == 0.0)
    check("零订单", orders == [])
    check("buys 空", plan.get("buys") == [])
    check("target_value 未设置（整段不执行）", "target_value" not in plan)


def test_t1_half():
    print("[R2] T=1 半仓（-1.2%）")
    orders, plan = run_case(hs300_env=-1.2)
    exp_tv = CAP * 0.5 / TOP_N
    exp_vol = int(exp_tv / 10.0 / MIN_LOT) * MIN_LOT
    check("tier=1", plan.get("tier") == 1)
    check("deploy_pct=0.5", plan.get("deploy_pct") == 0.5)
    check("target_value=资金池x0.5/TOP_N", abs(plan.get("target_value", 0) - exp_tv) < 0.01,
          "got %s exp %s" % (plan.get("target_value"), exp_tv))
    check("买入按半仓预算", len(plan.get("buys", [])) == 1 and plan["buys"][0]["vol"] == exp_vol,
          "buys=%r" % plan.get("buys"))


def test_boundary_minus_1():
    print("[R3] 边界：恰 -1.0% 判满仓")
    _, plan = run_case(hs300_env=-1.0)
    check("tier=2（<-1.0 严格小于）", plan.get("tier") == 2)


def test_t2_unchanged():
    print("[R4] T=2 满仓（+0.5%）：与旧公式行为零变化")
    orders, plan = run_case(hs300_env=0.5)
    exp_tv_old = CAP * (1 - GCFG.RESERVE_CASH_PCT) / TOP_N   # 旧公式
    exp_tv_new = CAP * RBG.G.DEPLOY_PCT / TOP_N              # 新公式（deploy_pct=0.98）
    exp_vol = int(exp_tv_new / 10.0 / MIN_LOT) * MIN_LOT
    check("tier=2", plan.get("tier") == 2)
    check("deploy_pct=0.98", abs(plan.get("deploy_pct", 0) - 0.98) < 1e-9)
    check("新旧公式 target_value 逐分一致", abs(exp_tv_old - exp_tv_new) < 1e-9,
          "old=%s new=%s" % (exp_tv_old, exp_tv_new))
    check("plan.target_value 正确", abs(plan.get("target_value", 0) - exp_tv_new) < 0.01)
    check("买入量不变", len(plan.get("buys", [])) == 1 and plan["buys"][0]["vol"] == exp_vol,
          "buys=%r" % plan.get("buys"))


def test_failsafe():
    print("[R5] fail-safe：沪深300 取数失败 → T=1 半仓")
    _, plan = run_case(hs300_none=True)
    check("tier=1", plan.get("tier") == 1)
    check("deploy_pct=0.5", plan.get("deploy_pct") == 0.5)
    check("hs300_pct=None 如实落档", plan.get("hs300_pct") is None)


def test_plan_fields():
    print("[R6] plan 溯源三字段")
    _, plan = run_case(hs300_env=-2.0)
    check("hs300_pct=-2.0", plan.get("hs300_pct") == -2.0)
    check("tier 落档", "tier" in plan)
    check("deploy_pct 落档", "deploy_pct" in plan)


def test_t1_kept_trim():
    print("[R7] T=1 下 kept 超配减仓同按半仓目标（G2 同口径）")
    ledger = {"600000.SH": {"vol": 3000, "cost": 10.0, "src": "cfg"}}
    hold_dates = {"600000.SH": "20261008"}
    orders, plan = run_case(hs300_env=-1.2, ledger=ledger, hold_dates=hold_dates)
    exp_tv = CAP * 0.5 / TOP_N
    exp_kept_vol = int(exp_tv / 10.0 / MIN_LOT) * MIN_LOT      # 1000 股
    exp_trim = 3000 - exp_kept_vol                              # 2000 股
    exp_buy_vol = int(exp_tv / 10.0 / MIN_LOT) * MIN_LOT
    sells = [s for s in plan.get("sells", []) if s.get("note") == "等权减仓"]
    check("超配减仓触发", len(sells) == 1, "sells=%r" % plan.get("sells"))
    if sells:
        check("减仓量=3000-半仓目标(%d)=%d" % (exp_kept_vol, exp_trim), sells[0]["vol"] == exp_trim,
              "got %s" % sells[0]["vol"])
    check("新晋买入半仓预算", len(plan.get("buys", [])) == 1 and plan["buys"][0]["vol"] == exp_buy_vol,
          "buys=%r" % plan.get("buys"))
    check("先卖后买（SELL 在前）", orders and orders[0]["action"] == "SELL",
          "orders[0]=%r" % (orders[0] if orders else None))
    check("到期卖出未误触发（持有未满10日）",
          not any("融合持有到期" in o.get("reason", "") for o in orders))


def test_t0_no_rebalance():
    print("[R8] T=0 下等权减仓/建仓整段不执行（kept 不被动减仓）")
    ledger = {"600000.SH": {"vol": 3000, "cost": 10.0, "src": "cfg"}}
    hold_dates = {"600000.SH": "20261008"}
    orders, plan = run_case(hs300_env=-2.0, ledger=ledger, hold_dates=hold_dates)
    check("零订单（无到期卖/无减仓/无买入）", orders == [], "orders=%r" % orders)
    check("buys 空", plan.get("buys") == [])
    check("sells 空", plan.get("sells") == [])


def test_frozen_t0_combo():
    print("[R9] 降级闸 x T=0 双闸共存")
    orders, plan = run_case(hs300_env=-2.0, frozen=True)
    check("downgrade_frozen=True", plan.get("downgrade_frozen") is True)
    check("tier=0", plan.get("tier") == 0)
    check("deploy_pct=0.0", plan.get("deploy_pct") == 0.0)
    check("零订单", orders == [])


if __name__ == "__main__":
    test_t0_stop_buy()
    test_t1_half()
    test_boundary_minus_1()
    test_t2_unchanged()
    test_failsafe()
    test_plan_fields()
    test_t1_kept_trim()
    test_t0_no_rebalance()
    test_frozen_t0_combo()
    print()
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    sys.exit(1 if FAIL else 0)
