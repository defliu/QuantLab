# -*- coding: utf-8 -*-
"""T-20261012-001 ST 风险警示剔除测试：PF 单元（load_st_codes/st_guard）+ G2/ENS 集成。

背景（诚哥拍板 2026-10-12）：候选链特征快照（g2_latest_features.parquet 等）无 is_st
列，候选生成端完全看不到 ST 状态 → 当日 G2 臂实盘买入 *ST华鹏 603021.SH（7600 股
@4.17）。拍板处置：存量 ST 持仓（G2 603021 / ENS 000610）次日换仓日开盘强制卖出，
不等持有期；三链候选生成端（deploy_predict*.py / review_full.py）+ 执行端买入
（rebalance_g2/ens/daily st_guard）双侧剔除。

三层防线（实现落点 position_filter.py）：
  L1 候选生成端：load_st_codes（主库 is_st 快照，滞后约 2 周）批量剔除
  L2 执行端买入：st_guard 双层拦截（主库快照 + 腾讯实时名称，抓新戴帽）
  L3 执行端卖出：ST 持仓强制 SELL（不等 HOLD_DAYS），T+1 锁定顺延次日

测试项：
  S1  is_st_name：*ST/ST/S*ST 前缀判定，正常中文名/空/None 放行
  S2  load_st_codes：主库最新数据日快照取 ST 集；缓存生效；读取失败 None（fail-open）
  S3  st_guard 主库层命中（name 参数不干扰短路）
  S4  st_guard 实时名称层命中（主库未标记=新戴帽场景）
  S5  st_guard 正常股放行
  S6  st_guard name 未传时现取腾讯兜底
  S7  st_guard 双层皆失 fail-open 放行
  S8  G2 卖出：ST 持仓持有 1 日（<HOLD_DAYS）仍强制 SELL（不等持有期）
  S8b G2 卖出：ST 持仓 T+1 锁定（可卖 0）→ skip 顺延次日，零订单
  S9  G2 买入：候选池首位 ST 被拦截，次位正常股照常买入
  S10 ENS 卖出：ST 持仓（000610 场景）持有 6 日（<HOLD_DAYS）强制 SELL
  S11 ENS 买入：候选池首位 ST（600180 场景）被拦截，次位照常买入

注：rebalance_daily.py（V1.x）的 ST 逻辑与 G2/ENS 同构（PF.st_guard 同一入口），
其 main() 强依赖 xtquant 连接无法离线单测，由 S1-S7 单元 + 同构代码评审覆盖；
V1.x 当前空仓且候选全正常，无存量 ST。

跑法：miniqmt venv python research/tests/test_st_exclusion.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJ)

import pandas as pd

import g2_config as G2CFG
import g2_ens_config as GECFG
import paper_forward_downgrade as PFD
import position_filter as PF
import rebalance_g2 as RG
import rebalance_ens as E

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


DATE = "20261012"
CAP = 100000.0
ST_CODE_G2 = "603021.SH"   # *ST华鹏（10-12 实盘买入实锤）
ST_CODE_ENS = "000610.SZ"  # ENS 存量 ST 持仓


def _reset_st_cache(codes=None):
    PF._ST_CACHE["codes"] = codes
    PF._ST_CACHE["asof"] = "20260930" if codes is not None else None


# ============================================================ S1-S7 单元（PF）

def test_is_st_name():
    print("[S1] is_st_name 名称判定")
    check("*ST前缀", PF.is_st_name("*ST华鹏"))
    check("ST前缀", PF.is_st_name("ST中迪"))
    check("S*ST前缀", PF.is_st_name("S*ST前锋"))
    check("正常中文名放行", not PF.is_st_name("金发拉比"))
    check("空串放行", not PF.is_st_name(""))
    check("None放行", not PF.is_st_name(None))


def test_load_st_codes():
    print("[S2] load_st_codes 主库快照（最新数据日）")
    idx = pd.MultiIndex.from_tuples([
        ("20260929", "000001.SZ"), ("20260929", "603021.SH"),
        ("20260930", "000001.SZ"), ("20260930", "603021.SH"), ("20260930", "600180.SH"),
    ], names=["trade_date", "ts_code"])
    df = pd.DataFrame({"is_st": [0, 1, 0, 1, 1]}, index=idx)
    orig_rp = PF.pd.read_parquet
    PF.pd.read_parquet = lambda path, columns=None: df
    try:
        _reset_st_cache(None)
        codes = PF.load_st_codes()
        check("返回最新日ST集合", codes == {"603021.SH", "600180.SH"}, str(codes))
        check("asof=最新数据日", PF._ST_CACHE["asof"] == "20260930")
        check("缓存生效(二次调用同对象)", PF.load_st_codes() is codes)

        def _boom(path, columns=None):
            raise IOError("no file")
        PF.pd.read_parquet = _boom
        _reset_st_cache(None)
        check("读取失败返回None(fail-open)", PF.load_st_codes() is None)
    finally:
        PF.pd.read_parquet = orig_rp
        _reset_st_cache(None)


def test_st_guard_master():
    print("[S3] st_guard 主库层命中（name 不干扰短路）")
    _reset_st_cache({ST_CODE_G2})
    skip, why = PF.st_guard(ST_CODE_G2, name="金发拉比")
    check("拦截", skip)
    check("reason含主库快照", why is not None and "主库is_st快照" in why, str(why))


def test_st_guard_name_layer():
    print("[S4] st_guard 实时名称层命中（主库未标记=新戴帽）")
    _reset_st_cache(set())
    skip, why = PF.st_guard(ST_CODE_G2, name="*ST华鹏")
    check("拦截", skip)
    check("reason含实时名称", why is not None and "*ST华鹏" in why, str(why))


def test_st_guard_pass():
    print("[S5] st_guard 正常股放行")
    _reset_st_cache({ST_CODE_G2})
    skip, why = PF.st_guard("000001.SZ", name="平安银行")
    check("放行且无reason", (not skip) and why is None)


def test_st_guard_fetch_fallback():
    print("[S6] st_guard name 未传时现取腾讯兜底")
    _reset_st_cache(set())
    orig_fn = PF.fetch_name
    PF.fetch_name = lambda code: "*ST华鹏"
    try:
        skip, why = PF.st_guard(ST_CODE_G2)
        check("现取命中", skip and why is not None and "*ST华鹏" in why, str(why))
    finally:
        PF.fetch_name = orig_fn


def test_st_guard_fail_open():
    print("[S7] st_guard 双层皆失 fail-open 放行")
    orig_rp = PF.pd.read_parquet

    def _boom(path, columns=None):
        raise IOError("no file")
    PF.pd.read_parquet = _boom
    orig_fn = PF.fetch_name
    PF.fetch_name = lambda code: None
    try:
        _reset_st_cache(None)
        skip, why = PF.st_guard(ST_CODE_G2)
        check("fail-open放行", (not skip) and why is None)
    finally:
        PF.pd.read_parquet = orig_rp
        PF.fetch_name = orig_fn
        _reset_st_cache(None)


# ==================================================== S8-S11 集成（build_plan）

RG_ORIG = {
    "load_g2_selection": RG.load_g2_selection,
    "load_g2_ledger": RG.load_g2_ledger,
    "load_hold_dates": RG.load_hold_dates,
    "fetch_price": RG.fetch_price,
    "read_heart": RG.read_heart,
    "load_sellable": RG.load_sellable,
    "_fetch_ohlc": RG._fetch_ohlc,
    "_count_trade_days": RG._count_trade_days,
    "pfd_is_frozen": PFD.is_frozen,
    "pf_st_guard": PF.st_guard,
    "ens_hold_dates_file": G2CFG.ENS_HOLD_DATES_FILE,
}

E_ORIG = {
    "load_ens_selection": E.load_ens_selection,
    "load_ens_ledger": E.load_ens_ledger,
    "load_hold_dates": E.load_hold_dates,
    "fetch_price": E.fetch_price,
    "read_heart": E.read_heart,
    "load_sellable": E.load_sellable,
    "_fetch_ohlc": E._fetch_ohlc,
    "_count_trade_days": E._count_trade_days,
    "pfd_is_frozen": PFD.is_frozen,
    "pf_st_guard": PF.st_guard,
    "g2_hold_dates_file": GECFG.G2_HOLD_DATES_FILE,
}


def _st_stub_factory(st_set):
    """st_guard 桩：命中集内 (True, reason)，集外 (False, None)。"""
    def _stub(code, name=None):
        if code in st_set:
            return True, "ST风险警示剔除(*ST华鹏)"
        return False, None
    return _stub


def run_g2_case(ledger=None, hold_dates=None, pool=None, st_set=None, sellable=100000,
                held_days=1):
    """桩化 rebalance_g2 外部依赖后跑 build_plan，返回 (orders, plan)。"""
    meta = RG._candidate_meta(DATE, None, "none")
    RG.load_g2_selection = lambda d, t: (list(pool or []), "test pool", meta)
    RG.load_g2_ledger = lambda d: dict(ledger or {})
    RG.load_hold_dates = lambda: dict(hold_dates or {})
    RG.fetch_price = lambda code: 10.0
    RG.read_heart = lambda date=None: {}
    RG.load_sellable = lambda d, c: sellable
    RG._fetch_ohlc = lambda code: None
    RG._count_trade_days = lambda s, e: (held_days, True)
    PFD.is_frozen = lambda s: False
    PF.st_guard = _st_stub_factory(set(st_set or ()))
    G2CFG.ENS_HOLD_DATES_FILE = os.path.join(tempfile.gettempdir(), "nonexistent_enshd_st_test.json")
    saved_env = os.environ.get("G2_HS300_PCT")
    try:
        os.environ["G2_HS300_PCT"] = "0.5"   # T=2 满仓（买卖通道全开）
        orders, plan = RG.build_plan(DATE, CAP)
    finally:
        RG.load_g2_selection = RG_ORIG["load_g2_selection"]
        RG.load_g2_ledger = RG_ORIG["load_g2_ledger"]
        RG.load_hold_dates = RG_ORIG["load_hold_dates"]
        RG.fetch_price = RG_ORIG["fetch_price"]
        RG.read_heart = RG_ORIG["read_heart"]
        RG.load_sellable = RG_ORIG["load_sellable"]
        RG._fetch_ohlc = RG_ORIG["_fetch_ohlc"]
        RG._count_trade_days = RG_ORIG["_count_trade_days"]
        PFD.is_frozen = RG_ORIG["pfd_is_frozen"]
        PF.st_guard = RG_ORIG["pf_st_guard"]
        G2CFG.ENS_HOLD_DATES_FILE = RG_ORIG["ens_hold_dates_file"]
        if saved_env is None:
            os.environ.pop("G2_HS300_PCT", None)
        else:
            os.environ["G2_HS300_PCT"] = saved_env
    return orders, plan


def run_ens_case(ledger=None, hold_dates=None, pool=None, st_set=None, sellable=100000,
                 held_days=6):
    """桩化 rebalance_ens 外部依赖后跑 build_plan，返回 (orders, plan)。"""
    meta = E._candidate_meta(DATE, None, "none")
    E.load_ens_selection = lambda d, t: (list(pool or []), "test pool", meta)
    E.load_ens_ledger = lambda d: dict(ledger or {})
    E.load_hold_dates = lambda: dict(hold_dates or {})
    E.fetch_price = lambda code: 10.0
    E.read_heart = lambda date=None: {}
    E.load_sellable = lambda d, c: sellable
    E._fetch_ohlc = lambda code: None
    E._count_trade_days = lambda s, e: (held_days, True)
    PFD.is_frozen = lambda s: False
    PF.st_guard = _st_stub_factory(set(st_set or ()))
    GECFG.G2_HOLD_DATES_FILE = os.path.join(tempfile.gettempdir(), "nonexistent_g2hd_st_test.json")
    saved_env = os.environ.get("G2_HS300_PCT")
    try:
        os.environ["G2_HS300_PCT"] = "0.5"   # T=2 满仓（买卖通道全开）
        orders, plan = E.build_plan(DATE, CAP)
    finally:
        E.load_ens_selection = E_ORIG["load_ens_selection"]
        E.load_ens_ledger = E_ORIG["load_ens_ledger"]
        E.load_hold_dates = E_ORIG["load_hold_dates"]
        E.fetch_price = E_ORIG["fetch_price"]
        E.read_heart = E_ORIG["read_heart"]
        E.load_sellable = E_ORIG["load_sellable"]
        E._fetch_ohlc = E_ORIG["_fetch_ohlc"]
        E._count_trade_days = E_ORIG["_count_trade_days"]
        PFD.is_frozen = E_ORIG["pfd_is_frozen"]
        PF.st_guard = E_ORIG["pf_st_guard"]
        GECFG.G2_HOLD_DATES_FILE = E_ORIG["g2_hold_dates_file"]
        if saved_env is None:
            os.environ.pop("G2_HS300_PCT", None)
        else:
            os.environ["G2_HS300_PCT"] = saved_env
    return orders, plan


def test_g2_st_force_sell():
    print("[S8] G2 卖出：ST 持仓持有1日(<HOLD_DAYS=%d) 强制 SELL" % G2CFG.HOLD_DAYS)
    orders, plan = run_g2_case(
        ledger={ST_CODE_G2: {"vol": 7600, "cost": 4.17}},
        hold_dates={ST_CODE_G2: "20261012"},
        st_set={ST_CODE_G2}, held_days=1)
    st_sells = [s for s in plan.get("sells", []) if s["code"] == ST_CODE_G2]
    check("plan.sells 含 ST 强制卖出", len(st_sells) == 1, str(plan.get("sells")))
    st_orders = [o for o in orders if o.get("code") == ST_CODE_G2]
    check("订单含 SELL", len(st_orders) == 1 and st_orders[0]["action"] == "SELL", str(orders))
    check("卖出量=持仓量", st_orders and st_orders[0]["vol"] == 7600)
    check("reason含强制卖出", st_orders and "强制卖出" in st_orders[0].get("reason", ""),
          str(st_orders[0].get("reason") if st_orders else None))
    st_skips = [sk for sk in plan.get("skips", []) if sk.get("code") == ST_CODE_G2]
    check("无ST skip（已卖出）", st_skips == [], str(st_skips))


def test_g2_st_sell_t1_locked():
    print("[S8b] G2 卖出：ST 持仓 T+1 锁定（可卖0）→ skip 顺延次日，零订单")
    orders, plan = run_g2_case(
        ledger={ST_CODE_G2: {"vol": 7600, "cost": 4.17}},
        hold_dates={ST_CODE_G2: "20261012"},
        st_set={ST_CODE_G2}, sellable=0, held_days=1)
    check("零订单", orders == [], str(orders))
    st_skips = [sk for sk in plan.get("skips", []) if sk.get("code") == ST_CODE_G2]
    check("skip reason 含 T+1 顺延", len(st_skips) == 1 and "T+1锁定" in st_skips[0].get("reason", ""),
          str(st_skips))


def test_g2_st_buy_blocked():
    print("[S9] G2 买入：候选池首位 ST 被拦截，次位正常股照常买入")
    orders, plan = run_g2_case(
        ledger={}, hold_dates={},
        pool=[{"code": ST_CODE_G2, "total": 88.0}, {"code": "600001.SH", "total": 70.0}],
        st_set={ST_CODE_G2})
    st_buys = [b for b in plan.get("buys", []) if b["code"] == ST_CODE_G2]
    check("ST 未进 buys", st_buys == [], str(plan.get("buys")))
    st_skips = [sk for sk in plan.get("skips", []) if sk.get("code") == ST_CODE_G2]
    check("ST 落 skips 带 reason", len(st_skips) == 1 and "ST风险警示剔除" in st_skips[0].get("reason", ""),
          str(st_skips))
    st_orders = [o for o in orders if o.get("code") == ST_CODE_G2]
    check("ST 零订单", st_orders == [], str(orders))
    ok_orders = [o for o in orders if o.get("code") == "600001.SH" and o.get("action") == "BUY"]
    check("次位正常股照常 BUY", len(ok_orders) == 1, str(orders))


def test_ens_st_force_sell():
    print("[S10] ENS 卖出：ST 持仓(000610场景) 持有6日(<HOLD_DAYS=%d) 强制 SELL" % GECFG.HOLD_DAYS)
    orders, plan = run_ens_case(
        ledger={ST_CODE_ENS: {"vol": 2000, "cost": 3.0}},
        hold_dates={ST_CODE_ENS: "20260928"},
        st_set={ST_CODE_ENS}, held_days=6)
    st_sells = [s for s in plan.get("sells", []) if s["code"] == ST_CODE_ENS]
    check("plan.sells 含 ST 强制卖出", len(st_sells) == 1, str(plan.get("sells")))
    st_orders = [o for o in orders if o.get("code") == ST_CODE_ENS]
    check("订单含 SELL 2000 股", len(st_orders) == 1 and st_orders[0]["action"] == "SELL"
          and st_orders[0]["vol"] == 2000, str(orders))
    check("reason含强制卖出", st_orders and "强制卖出" in st_orders[0].get("reason", ""),
          str(st_orders[0].get("reason") if st_orders else None))


def test_ens_st_buy_blocked():
    print("[S11] ENS 买入：候选池首位 ST(600180场景) 被拦截，次位照常买入")
    orders, plan = run_ens_case(
        ledger={}, hold_dates={},
        pool=[{"code": "600180.SH", "total": 88.0}, {"code": "002762.SZ", "total": 70.0}],
        st_set={"600180.SH"})
    st_buys = [b for b in plan.get("buys", []) if b["code"] == "600180.SH"]
    check("ST 未进 buys", st_buys == [], str(plan.get("buys")))
    st_skips = [sk for sk in plan.get("skips", []) if sk.get("code") == "600180.SH"]
    check("ST 落 skips 带 reason", len(st_skips) == 1 and "ST风险警示剔除" in st_skips[0].get("reason", ""),
          str(st_skips))
    ok_orders = [o for o in orders if o.get("code") == "002762.SZ" and o.get("action") == "BUY"]
    check("次位正常股照常 BUY", len(ok_orders) == 1, str(orders))


if __name__ == "__main__":
    test_is_st_name()
    test_load_st_codes()
    test_st_guard_master()
    test_st_guard_name_layer()
    test_st_guard_pass()
    test_st_guard_fetch_fallback()
    test_st_guard_fail_open()
    test_g2_st_force_sell()
    test_g2_st_sell_t1_locked()
    test_g2_st_buy_blocked()
    test_ens_st_force_sell()
    test_ens_st_buy_blocked()
    print("=" * 60)
    print("PASS %d / FAIL %d" % (PASS[0], len(FAIL)))
    if FAIL:
        print("FAILED: %s" % FAIL)
        sys.exit(1)
    print("ALL PASS")
