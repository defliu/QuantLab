# -*- coding: utf-8 -*-
"""V1.3 风控桥「pending 订单状态机」集成测试。

MOCK 测不到 QMT 集成层是已知盲区（AGENTS.md：约 8 成 bug 在时序/异步/字段/生命周期），
本测试用桩把集成层语义固定下来，重点回归第3 项改造是否破坏了原有状态机行为：

  S1. 1a 成交量硬事实优先：deal 满量即 FILLED（哪怕状态码还没到 56）
  S2. 1b status=57 废单 -> 重报剩余量，且委托号累计
  S3. 1b' status=55 部成是活跃态，绝不当废单（防双倍建仓，2026-09-02 P0）
  S4. 1c status in (53,54) -> CANCELED 收尾
  S5. 1d 部成回写 PARTIAL_FILLED，且保留 pending（不误当终态）
  S6. 超时撤单：撤前已全成 -> FILLED，绝不重报
  S7. 超时撤单未死透 -> 延后复查，不重报不计数（防双倍持仓）
  S8. 重试前必须刷新最新价（不用陈旧触发价）
  S9. retry 耗尽 -> ABANDONED
  S10. 撤单确认三态：FILLED / CANCELED / REJECTED / UNCONFIRMED
  S11. 撤单未生效（仍活跃）-> 保留 pending + 重发撤单
  S12. 所有终态出口的 fills 记录必须走统一成交价协议（px_source 存在）
"""
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(PROJ, "strategy", "strategy_p16_v13_risk_src.py")

PASS = [0]
FAIL = []


def check(name, cond, extra=""):
    if cond:
        PASS[0] += 1
    else:
        FAIL.append("%s %s" % (name, extra))
        print("[FAIL] %s %s" % (name, extra))


class FakeTime(object):
    def __init__(self):
        self.slept = []
        self._t = 1000000.0

    def sleep(self, s):
        self.slept.append(s)
        self._t += s

    def time(self):
        return self._t

    def strftime(self, fmt):
        return "2026-10-02 09:30:00"


class Order(object):
    """大 QMT 订单对象桩。"""

    def __init__(self, oid="", status=50, deal_vol=0, sysid="", avg=0.0, px=0.0):
        self.m_nOrderID = oid
        self.m_nOrderStatus = status
        self.m_nDealVolume = deal_vol
        self.m_strSysid = sysid
        if avg:
            self.m_dAvgPrice = avg
        if px:
            self.m_dPrice = px


class Deal(object):
    def __init__(self, code, oid, vol, px):
        self.m_strInstrumentID = code
        self.m_nOrderID = oid
        self.m_nVolume = vol
        self.m_dAvgPrice = px


class Ctx(object):
    """QMT 上下文桩。"""

    def __init__(self, ticks=None):
        self._ticks = ticks or {}
        self.orders = []
        self.passed = []

    def get_full_tick(self, codes):
        return {c: {"lastPrice": v} for c, v in self._ticks.items()}

    def get_trade_detail_data(self, acct, mkt, tp):
        return getattr(self, "deals", [])


def load():
    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("v13_sm_under_test")
    mod.__file__ = SRC
    exec(compile(src, SRC, "exec"), mod.__dict__)
    mod.g = mod.__dict__
    mod.g["time"] = FakeTime()
    return mod


def new_pending(m, vol=1000, oid="1001", **kw):
    info = {
        "code": "300413.SZ",
        "bridge_code": "300413.SZ",
        "action": "SELL",
        "vol": vol,
        "cur_vol": vol,
        "filled_so_far": 0,
        "price": 9.50,
        "trigger_price": 9.50,
        "cost_price": 10.00,
        "last_price": 9.50,
        "retry": 0,
        "order_id": oid,
        "order_ids": [oid] if oid else [],
        "sysid": "SYS1",
        "status": "PENDING",
        "risk_reason": "stop_loss",
    }
    info.update(kw)
    m._g_pending["s1"] = info
    return info


def reset(m):
    m._g_pending.clear()
    m._g_today_fill_count = 0
    m._g_today_abandon_count = 0
    m._g_slippage_alert_count = 0
    m._g_deal_ok = None
    m._g_deal_err_count = 0
    m._g_deal_no_oid = False
    m.written = []
    m.orders = []
    m.cancels = []
    # 自闭环（2026-10-15 T-20261015-004）：V13 的 _add_or_update_fill / fills 文件回写
    # 已删除，唯一留痕出口是 _write_fill 内部的 print。测试通过包装 _write_fill
    # 捕获其返回的 rec（与生产同一对象），保持断言语义不变。
    # 注意：只包装一次——重复 reset 若基于「已被包装的 _write_fill」再包一层，
    # 一次调用会追加多条，断言（statuses == ["FILLED"] 等）会假失败。
    if not hasattr(m, "_write_fill_orig"):
        m._write_fill_orig = m._write_fill

        def _capture(*a, **kw):
            rec = m._write_fill_orig(*a, **kw)
            m.written.append(rec)
            return rec
        m._write_fill = _capture
    m._do_passorder = lambda C, action, code, v, price, prtype=None: (
        m.orders.append((action, code, v, price)) or "retry-oid-%d" % len(m.orders))
    m._cancel_order_by_id = lambda C, code, oid, sysid: (
        m.cancels.append((code, oid, sysid)) or True)


def statuses(w):
    return [r["status"] for r in w]


# ------------------------------------------------------------------ S1
def test_s1_filled_by_volume(m):
    reset(m)
    new_pending(m, vol=1000)
    # deal 已满量但状态码仍是 50（QMT 状态更新滞后）→ 成交量硬事实优先
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 1000, 9.40)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 1000, "SYS1", avg=9.40))
    m._check_pending_orders(C, "20261002")
    check("S1 满量即FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("S1 pending已清", "s1" not in m._g_pending, list(m._g_pending))
    check("S1 用真实成交均价", abs(m.written[0]["price"] - 9.40) < 1e-9, m.written[0]["price"])
    check("S1 价源=fill_avg", m.written[0]["px_source"] == "fill_avg", m.written[0]["px_source"])
    check("S1 计数+1", m._g_today_fill_count == 1, m._g_today_fill_count)


# ------------------------------------------------------------------ S2
def test_s2_rejected_retry(m):
    reset(m)
    new_pending(m, vol=1000)
    C = Ctx({"300413.SZ": 9.10})
    C.deals = []
    seq = {"n": 0}

    def lookup(*a, **k):
        seq["n"] += 1
        if seq["n"] == 1:
            return ("1001", Order("1001", 57, 0, "SYS1"))
        return ("1002", Order("1002", 50, 0, "SYS2"))

    m._lookup_order = lookup
    # 废单后必须死透才重报：第一次查 57（死透），重报
    m._handle_rejected_retry(C, "20261002", "s1", m._g_pending["s1"],
                             m.time.time(), 0)
    info = m._g_pending["s1"]
    check("S2 废单触发重报", len(m.orders) == 1, len(m.orders))
    check("S2 重报剩余量=1000", m.orders and m.orders[0][2] == 1000,
          m.orders[0] if m.orders else None)
    check("S2 retry=1", info["retry"] == 1, info["retry"])
    check("S2 委托号累计", "1002" in info["order_ids"], info["order_ids"])


# ------------------------------------------------------------------ S3
def test_s3_partial_not_rejected(m):
    reset(m)
    new_pending(m, vol=1000)
    C = Ctx()
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 55, 400, "SYS1"))
    m._check_pending_orders(C, "20261002")
    check("S3 status55不当废单", len(m.orders) == 0, len(m.orders))
    check("S3 pending保留", "s1" in m._g_pending, list(m._g_pending))
    check("S3 记PARTIAL_FILLED", "PARTIAL_FILLED" in statuses(m.written), statuses(m.written))


# ------------------------------------------------------------------ S4
def test_s4_canceled(m):
    reset(m)
    new_pending(m, vol=1000)
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 300, 9.40)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 54, 300, "SYS1", avg=9.40))
    m._check_pending_orders(C, "20261002")
    check("S4 撤类终态CANCELED", statuses(m.written) == ["CANCELED"], statuses(m.written))
    check("S4 CANCELED带成交量", m.written[0]["vol"] == 300, m.written[0]["vol"])
    check("S4 pending已清", "s1" not in m._g_pending)


# ------------------------------------------------------------------ S5
def test_s5_partial_then_timeout(m):
    reset(m)
    info = new_pending(m, vol=1000)
    info["time"] = m.time.time() - 10000  # 已超时
    C = Ctx({"300413.SZ": 9.00})
    C.deals = [Deal("300413.SZ", "1001", 400, 9.40)]
    st = {"n": 0}

    def lookup(*a, **k):
        st["n"] += 1
        # 撤单确认轮询：第2次起报死透
        if st["n"] <= 1:
            return ("1001", Order("1001", 55, 400, "SYS1"))
        return ("1001", Order("1001", 54, 400, "SYS1"))

    m._lookup_order = lookup
    m._check_pending_orders(C, "20261002")
    check("S5 部成先回写", statuses(m.written)[:1] == ["PARTIAL_FILLED"], statuses(m.written))
    # 【KNOWN-RISK 记录，非本项范围】现状 remaining = vol - filled_so_far，
    # 而 1d 部成分支不把 deal_vol 累加进 filled_so_far；死透轮询虽拿到 dv2=400，
    # 但只在 dv2>=cur_vol(全成交) 时使用，否则丢弃 → 重报量按 vol 满额算。
    # 即「部成 400 + 超时撤单死透」会重报 1000 而非 600，存在超卖 400 股的风险。
    # git show HEAD 证实该表达式在本次改造前后完全一致（既有行为，非第3项引入）。
    # 按「卖出重试改动须格外保守 + 最小改动」，本项不改实盘逻辑，仅固定现状并显式留痕。
    check("S5 重报量按vol满额(KNOWN-RISK)", m.orders and m.orders[0][2] == 1000,
          m.orders[0] if m.orders else None)


# ------------------------------------------------------------------ S6
def test_s6_filled_before_cancel(m):
    reset(m)
    info = new_pending(m, vol=1000)
    info["time"] = m.time.time() - 10000
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 1000, 9.30)]
    st = {"n": 0}

    def lookup(*a, **k):
        st["n"] += 1
        return ("1001", Order("1001", 50, 1000, "SYS1", avg=9.30))

    m._lookup_order = lookup
    m._handle_timeout_retry(C, "20261002", "s1", info, m.time.time())
    check("S6 撤前已全成->FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("S6 绝不重报", len(m.orders) == 0, len(m.orders))
    check("S6 pending已清", "s1" not in m._g_pending)


# ------------------------------------------------------------------ S7
def test_s7_cancel_not_dead(m):
    reset(m)
    info = new_pending(m, vol=1000)
    C = Ctx()
    C.deals = []
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 0, "SYS1"))
    m._handle_timeout_retry(C, "20261002", "s1", info, m.time.time())
    check("S7 未死透不重报", len(m.orders) == 0, len(m.orders))
    check("S7 保留pending", "s1" in m._g_pending, list(m._g_pending))
    check("S7 不写终态fills", m.written == [], statuses(m.written))
    check("S7 延后复查时间已回拨",
          info["time"] > m.time.time() - m.PENDING_TIMEOUT, info["time"])


# ------------------------------------------------------------------ S8
def test_s8_refresh_price_on_retry(m):
    reset(m)
    new_pending(m, vol=1000, price=5.00)
    C = Ctx({"300413.SZ": 9.77})
    C.deals = []
    # 原单必须先确认死透(57)才会重报；重报后的新单是活跃态(50)。
    # 若恒返回 50，代码会正确走 REJECTED-UNCONFIRMED 不重报（那是 S7 的语义，测不到刷新价）。
    seq = {"n": 0}

    def lookup(*a, **k):
        seq["n"] += 1
        if seq["n"] == 1:
            return ("1001", Order("1001", 57, 0, "SYS1"))
        return ("1002", Order("1002", 50, 0, "SYS2"))

    m._lookup_order = lookup
    m._handle_rejected_retry(C, "20261002", "s1", m._g_pending["s1"], m.time.time(), 0)
    check("S8 重试用刷新价非陈旧价",
          m.orders and abs(m.orders[0][3] - 9.77) < 1e-9,
          m.orders[0] if m.orders else None)
    check("S8 last_price同步", abs(m._g_pending["s1"]["last_price"] - 9.77) < 1e-9,
          m._g_pending["s1"]["last_price"])


# ------------------------------------------------------------------ S9
def test_s9_retry_exhausted(m):
    reset(m)
    info = new_pending(m, vol=1000, retry=m.MAX_RETRY)
    C = Ctx()
    C.deals = []
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 57, 0, "SYS1"))
    m._handle_rejected_retry(C, "20261002", "s1", info, m.time.time(), 0)
    check("S9 耗尽->ABANDONED", statuses(m.written) == ["ABANDONED"], statuses(m.written))
    check("S9 不再重报", len(m.orders) == 0, len(m.orders))
    check("S9 pending已清", "s1" not in m._g_pending)
    check("S9 放弃计数+1", m._g_today_abandon_count == 1, m._g_today_abandon_count)


# ------------------------------------------------------------------ S10
def test_s10_cancel_confirm(m):
    # 10a 撤单确认时已全成 -> FILLED
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 1000, 9.20)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 56, 1000, "SYS1", avg=9.20))
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("S10a 撤单确认FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("S10a 用真实均价", abs(m.written[0]["price"] - 9.20) < 1e-9, m.written[0]["price"])

    # 10b 已撤 -> CANCELED
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 54, 200, "SYS1"))
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("S10b 撤单确认CANCELED", statuses(m.written) == ["CANCELED"], statuses(m.written))

    # 10c 废单 -> REJECTED
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 57, 0, "SYS1"))
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("S10c 撤单确认REJECTED", statuses(m.written) == ["REJECTED"], statuses(m.written))

    # 10d 查不到订单 -> UNCONFIRMED（外部以 positions 兜底）
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    m._lookup_order = lambda *a, **k: ("", None)
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("S10d 查不到->UNCONFIRMED", statuses(m.written) == ["UNCONFIRMED"], statuses(m.written))


# ------------------------------------------------------------------ S11
def test_s11_cancel_not_effective(m):
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    info["time"] = m.time.time() - 200
    C = Ctx()
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 0, "SYS1"))
    canceled = []
    m._cancel_order_by_id = lambda C, code, oid, sysid: canceled.append(oid)
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("S11 撤单未生效保留pending", "s1" in m._g_pending, list(m._g_pending))
    check("S11 未写终态", m.written == [], statuses(m.written))
    check("S11 重发撤单", len(canceled) == 1, canceled)
    check("S11 cancel_retry=1", info["cancel_retry"] == 1, info.get("cancel_retry"))

    # 二次调用已达上限 -> 不再重发
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("S11 撤单重发有上限", len(canceled) == 1, canceled)


# ------------------------------------------------------------------ S12
def test_s12_all_exits_use_px_protocol(m):
    """所有终态出口的 fills 记录必须带 px_source（统一成交价协议的唯一出口）。"""
    cases = [
        ("FILLED", lambda: Order("1001", 50, 1000, "SYS1")),
        ("CANCELED", lambda: Order("1001", 54, 300, "SYS1")),
        ("PARTIAL_FILLED", lambda: Order("1001", 55, 400, "SYS1")),
    ]
    for want, mk in cases:
        reset(m)
        new_pending(m, vol=1000)
        C = Ctx()
        C.deals = []
        o = mk()
        m._lookup_order = lambda *a, **k: ("1001", o)
        m._check_pending_orders(C, "20261002")
        if m.written:
            rec = m.written[-1]
            check("S12 %s 带px_source" % want, rec.get("px_source") in
                  ("fill_avg", "order_obj", "ref_price", "unresolved"), rec.get("px_source"))
            check("S12 %s 带trigger_price" % want, "trigger_price" in rec, list(rec.keys()))
            check("S12 %s 带gap_pct" % want, "gap_pct" in rec, list(rec.keys()))
            check("S12 %s 带order_ids" % want, "order_ids" in rec, list(rec.keys()))
            # 消费方可记账性：只有 FILLED/PARTIAL 且 price>0
            bookable = rec["status"] in ("FILLED", "PARTIAL_FILLED") and rec["price"] > 0
            expect = rec["status"] in ("FILLED", "PARTIAL_FILLED")
            check("S12 %s 记账门槛一致" % want, bookable == expect,
                  (rec["status"], rec["price"]))


def main():
    m = load()
    for fn in (test_s1_filled_by_volume, test_s2_rejected_retry,
               test_s3_partial_not_rejected, test_s4_canceled,
               test_s5_partial_then_timeout, test_s6_filled_before_cancel,
               test_s7_cancel_not_dead, test_s8_refresh_price_on_retry,
               test_s9_retry_exhausted, test_s10_cancel_confirm,
               test_s11_cancel_not_effective, test_s12_all_exits_use_px_protocol):
        try:
            fn(m)
        except Exception as e:
            import traceback
            traceback.print_exc()
            FAIL.append("%s 抛异常: %s" % (fn.__name__, e))
    print("=" * 60)
    print("PASS=%d FAIL=%d" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("  [FAIL] %s" % f)
    print("=" * 60)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())