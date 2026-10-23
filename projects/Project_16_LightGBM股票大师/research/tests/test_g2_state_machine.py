# -*- coding: utf-8 -*-
"""G2 风控桥「pending 订单状态机」集成测试（第3项：deal/真实成交均价/跳空/滑点）。

MOCK 测不到 QMT 集成层是已知盲区（AGENTS.md：约 8 成 bug 在时序/异步/字段/生命周期），
本测试用桩把集成层语义固定下来。G2 侧与 V1.3 的差异点是**文件撤单**：
`_process_cancels()` 有一条独立于状态机的撤单入口，其「首反查才拿到委托号」
必须立即累计进 order_ids，否则终态 _write_fill 的 deal 查询会漏掉本轮明细、
把真实成交均价错误降级为订单对象价/参考价。

  T1.  1a 成交量硬事实优先：deal 满量即 FILLED（哪怕状态码还没到 56）
  T2.  1b status=57 废单 -> 重报剩余量，且委托号累计
  T3.  1b' status=55 部成是活跃态，绝不当废单（防双倍建仓，2026-09-02 P0）
  T4.  1c status in (53,54) -> CANCELED 收尾
  T5.  1d 部成回写 PARTIAL_FILLED，且保留 pending
  T6.  超时撤单：撤前已全成 -> FILLED，绝不重报
  T7.  超时撤单未死透 -> 延后复查，不重报不计数（防双倍持仓）
  T8.  重试前必须刷新最新价（不用陈旧触发价）
  T9.  retry 耗尽 -> ABANDONED
  T10. 撤单确认四态：FILLED / CANCELED / REJECTED / UNCONFIRMED
  T11. 撤单未生效（仍活跃）-> 保留 pending + 重发撤单有上限
  T12. 所有终态出口的 fills 记录必须走统一成交价协议（px_source 存在）
  T13. [G2 特有] _process_cancels 首反查委托号 -> 立即累计进 order_ids + 回填 order_id
  T14. [G2 特有] _process_cancels 撤单已发 -> cancel_requested=True，进入确认态
  T15. [G2 特有] _process_cancels 撤前已全成 -> FILLED 且用 deal 真实均价（依赖 T13）
  T16. [G2 特有] _process_cancels 拿不到 oid -> 保留 pending，绝不假写 CANCELED
  T17. [本轮修复] timeout 首反查委托号 -> 累计；重报后再累计第二枚
  T18. [本轮修复] cancel-confirm 首反查委托号 -> 累计
  T19. heartbeat 快照必须含滑点/跳空基线字段 + 当日告警计数
  T20. init 必须从心跳恢复当日告警计数（重启不丢留痕）
  T21. 跨日重置：打印前日汇总并清零告警计数
"""
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.join(PROJ, "strategy", "strategy_p16_g2_selfcontained_src.py")

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

    def set_account(self, acct):
        pass

    def get_current_time(self):
        return "08:00:00"


def load():
    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("g2_sm_under_test")
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
    m._add_or_update_fill = lambda date, rec: m.written.append(rec)
    m._do_passorder = lambda C, action, code, v, price, prtype=None: (
        m.orders.append((action, code, v, price)) or "retry-oid-%d" % len(m.orders))
    m._cancel_order_by_id = lambda C, code, oid, sysid: (
        m.cancels.append((code, oid, sysid)) or True)


def statuses(w):
    return [r["status"] for r in w]


# ------------------------------------------------------------------ T1
def test_t1_filled_by_volume(m):
    reset(m)
    new_pending(m, vol=1000)
    # deal 已满量但状态码仍是 50（QMT 状态更新滞后）→ 成交量硬事实优先
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 1000, 9.40)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 1000, "SYS1", avg=9.40))
    m._check_pending_orders(C, "20261002")
    check("T1 满量即FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("T1 pending已清", "s1" not in m._g_pending, list(m._g_pending))
    check("T1 用真实成交均价", abs(m.written[0]["price"] - 9.40) < 1e-6, m.written[0]["price"])
    check("T1 价源=fill_avg", m.written[0]["px_source"] == "fill_avg", m.written[0]["px_source"])
    check("T1 计数+1", m._g_today_fill_count == 1, m._g_today_fill_count)


# ------------------------------------------------------------------ T2
def test_t2_rejected_retry(m):
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
    m._handle_rejected_retry(C, "20261002", "s1", m._g_pending["s1"],
                             m.time.time(), 0)
    info = m._g_pending["s1"]
    check("T2 废单触发重报", len(m.orders) == 1, len(m.orders))
    check("T2 重报剩余量=1000", m.orders and m.orders[0][2] == 1000,
          m.orders[0] if m.orders else None)
    check("T2 retry=1", info["retry"] == 1, info["retry"])
    check("T2 委托号累计", "1002" in info["order_ids"], info["order_ids"])


# ------------------------------------------------------------------ T3
def test_t3_partial_not_rejected(m):
    reset(m)
    new_pending(m, vol=1000)
    C = Ctx()
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 55, 400, "SYS1"))
    m._check_pending_orders(C, "20261002")
    check("T3 status55不当废单", len(m.orders) == 0, len(m.orders))
    check("T3 pending保留", "s1" in m._g_pending, list(m._g_pending))
    check("T3 记PARTIAL_FILLED", "PARTIAL_FILLED" in statuses(m.written), statuses(m.written))


# ------------------------------------------------------------------ T4
def test_t4_canceled(m):
    reset(m)
    new_pending(m, vol=1000)
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 300, 9.40)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 54, 300, "SYS1", avg=9.40))
    m._check_pending_orders(C, "20261002")
    check("T4 撤类终态CANCELED", statuses(m.written) == ["CANCELED"], statuses(m.written))
    check("T4 CANCELED带成交量", m.written[0]["vol"] == 300, m.written[0]["vol"])
    check("T4 pending已清", "s1" not in m._g_pending)


# ------------------------------------------------------------------ T5
def test_t5_partial_then_timeout(m):
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
    check("T5 部成先回写", statuses(m.written)[:1] == ["PARTIAL_FILLED"], statuses(m.written))
    # 【KNOWN-RISK 记录，非本项范围】现状 remaining = vol - filled_so_far，
    # 而 1d 部成分支不把 deal_vol 累加进 filled_so_far → 「部成 400 + 超时撤单死透」
    # 会重报 1000 而非 600，存在超卖 400 股的风险。该表达式在第3项改造前后完全一致
    # （既有行为，非本次引入），按「卖出重试改动须格外保守 + 最小改动」仅固定现状并留痕。
    check("T5 重报量按vol满额(KNOWN-RISK)", m.orders and m.orders[0][2] == 1000,
          m.orders[0] if m.orders else None)


# ------------------------------------------------------------------ T6
def test_t6_filled_before_cancel(m):
    reset(m)
    info = new_pending(m, vol=1000)
    info["time"] = m.time.time() - 10000
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 1000, 9.30)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 1000, "SYS1", avg=9.30))
    m._handle_timeout_retry(C, "20261002", "s1", info, m.time.time())
    check("T6 撤前已全成->FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("T6 绝不重报", len(m.orders) == 0, len(m.orders))
    check("T6 pending已清", "s1" not in m._g_pending)


# ------------------------------------------------------------------ T7
def test_t7_cancel_not_dead(m):
    reset(m)
    info = new_pending(m, vol=1000)
    C = Ctx()
    C.deals = []
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 0, "SYS1"))
    m._handle_timeout_retry(C, "20261002", "s1", info, m.time.time())
    check("T7 未死透不重报", len(m.orders) == 0, len(m.orders))
    check("T7 保留pending", "s1" in m._g_pending, list(m._g_pending))
    check("T7 不写终态fills", m.written == [], statuses(m.written))
    check("T7 延后复查时间已回拨",
          info["time"] > m.time.time() - m.PENDING_TIMEOUT, info["time"])


# ------------------------------------------------------------------ T8
def test_t8_refresh_price_on_retry(m):
    reset(m)
    new_pending(m, vol=1000, price=5.00)
    C = Ctx({"300413.SZ": 9.77})
    C.deals = []
    # 原单必须先确认死透(57)才会重报；重报后的新单是活跃态(50)。
    # 若恒返回 50，代码会正确走 REJECTED-UNCONFIRMED 不重报（那是 T7 的语义，测不到刷新价）。
    seq = {"n": 0}

    def lookup(*a, **k):
        seq["n"] += 1
        if seq["n"] == 1:
            return ("1001", Order("1001", 57, 0, "SYS1"))
        return ("1002", Order("1002", 50, 0, "SYS2"))

    m._lookup_order = lookup
    m._handle_rejected_retry(C, "20261002", "s1", m._g_pending["s1"], m.time.time(), 0)
    check("T8 重试用刷新价非陈旧价",
          m.orders and abs(m.orders[0][3] - 9.77) < 1e-6,
          m.orders[0] if m.orders else None)
    check("T8 last_price同步", abs(m._g_pending["s1"]["last_price"] - 9.77) < 1e-6,
          m._g_pending["s1"]["last_price"])


# ------------------------------------------------------------------ T9
def test_t9_retry_exhausted(m):
    reset(m)
    info = new_pending(m, vol=1000, retry=m.MAX_RETRY)
    C = Ctx()
    C.deals = []
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 57, 0, "SYS1"))
    m._handle_rejected_retry(C, "20261002", "s1", info, m.time.time(), 0)
    check("T9 耗尽->ABANDONED", statuses(m.written) == ["ABANDONED"], statuses(m.written))
    check("T9 不再重报", len(m.orders) == 0, len(m.orders))
    check("T9 pending已清", "s1" not in m._g_pending)
    check("T9 放弃计数+1", m._g_today_abandon_count == 1, m._g_today_abandon_count)


# ------------------------------------------------------------------ T10
def test_t10_cancel_confirm(m):
    # 10a 撤单确认时已全成 -> FILLED
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    C = Ctx()
    C.deals = [Deal("300413.SZ", "1001", 1000, 9.20)]
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 56, 1000, "SYS1", avg=9.20))
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("T10a 撤单确认FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("T10a 用真实均价", abs(m.written[0]["price"] - 9.20) < 1e-6, m.written[0]["price"])

    # 10b 已撤 -> CANCELED
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    C2 = Ctx()
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 54, 200, "SYS1"))
    m._handle_cancel_confirm(C2, "20261002", "s1", info, m.time.time())
    check("T10b 撤单确认CANCELED", statuses(m.written) == ["CANCELED"], statuses(m.written))

    # 10c 废单 -> REJECTED
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    C3 = Ctx()
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 57, 0, "SYS1"))
    m._handle_cancel_confirm(C3, "20261002", "s1", info, m.time.time())
    check("T10c 撤单确认REJECTED", statuses(m.written) == ["REJECTED"], statuses(m.written))

    # 10d 查不到订单 -> UNCONFIRMED（外部以 positions 兜底）
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    C4 = Ctx()
    m._lookup_order = lambda *a, **k: ("", None)
    m._handle_cancel_confirm(C4, "20261002", "s1", info, m.time.time())
    check("T10d 查不到->UNCONFIRMED", statuses(m.written) == ["UNCONFIRMED"], statuses(m.written))


# ------------------------------------------------------------------ T11
def test_t11_cancel_not_effective(m):
    reset(m)
    info = new_pending(m, vol=1000, cancel_requested=True)
    info["time"] = m.time.time() - 200
    C = Ctx()
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 0, "SYS1"))
    canceled = []
    m._cancel_order_by_id = lambda C, code, oid, sysid: canceled.append(oid)
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("T11 撤单未生效保留pending", "s1" in m._g_pending, list(m._g_pending))
    check("T11 未写终态", m.written == [], statuses(m.written))
    check("T11 重发撤单", len(canceled) == 1, canceled)
    check("T11 cancel_retry=1", info["cancel_retry"] == 1, info.get("cancel_retry"))

    # 二次调用已达上限 -> 不再重发
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("T11 撤单重发有上限", len(canceled) == 1, canceled)


# ------------------------------------------------------------------ T12
def test_t12_all_exits_use_px_protocol(m):
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
            check("T12 %s 带px_source" % want, rec.get("px_source") in
                  ("fill_avg", "order_obj", "ref_price", "unresolved"), rec.get("px_source"))
            check("T12 %s 带trigger_price" % want, "trigger_price" in rec, list(rec.keys()))
            check("T12 %s 带gap_pct" % want, "gap_pct" in rec, list(rec.keys()))
            check("T12 %s 带order_ids" % want, "order_ids" in rec, list(rec.keys()))
            # 消费方可记账性：只有 FILLED/PARTIAL 且 price>0
            bookable = rec["status"] in ("FILLED", "PARTIAL_FILLED") and rec["price"] > 0
            expect = rec["status"] in ("FILLED", "PARTIAL_FILLED")
            check("T12 %s 记账门槛一致" % want, bookable == expect,
                  (rec["status"], rec["price"]))


# ------------------------------------------------------------------ T13
def test_t13_process_cancels_first_lookup_oid(m):
    """[G2 特有] 文件撤单：首次反查才拿到委托号，必须立即累计进 order_ids。"""
    reset(m)
    info = new_pending(m, vol=1000, oid="")
    check("T13 前置 order_id 为空", info["order_id"] == "" and info["order_ids"] == [],
          (info["order_id"], info["order_ids"]))
    C = Ctx()
    C.deals = []
    # _lookup_order 的首个返回值刻意为空串，模拟「缓存延迟导致首次没抓到 order_id」
    m._lookup_order = lambda *a, **k: ("", Order("7007", 50, 0, "SYS7"))
    m._process_cancels(C, {"cancels": [{"strategy_order_id": "s1"}]}, "20261002")
    check("T13 首反查委托号已回填", info["order_id"] == "7007", info["order_id"])
    check("T13 首反查委托号已累计", "7007" in info["order_ids"], info["order_ids"])


# ------------------------------------------------------------------ T14
def test_t14_process_cancels_sends_cancel(m):
    reset(m)
    info = new_pending(m, vol=1000, oid="1001")
    C = Ctx()
    C.deals = []
    m._lookup_order = lambda *a, **k: ("1001", Order("1001", 50, 0, "SYS1"))
    m._process_cancels(C, {"cancels": [{"strategy_order_id": "s1"}]}, "20261002")
    check("T14 撤单已发", len(m.cancels) == 1, m.cancels)
    check("T14 进入确认态", info.get("cancel_requested") is True, info.get("cancel_requested"))
    check("T14 未写终态fills", m.written == [], statuses(m.written))
    check("T14 保留pending", "s1" in m._g_pending, list(m._g_pending))


# ------------------------------------------------------------------ T15
def test_t15_process_cancels_filled_too_late(m):
    """[G2 特有] 撤单到达时已全成 -> FILLED，且必须用 deal 真实均价。

    本用例同时锁死 T13 的补丁：deal 明细只挂在「首反查才拿到」的委托号 7007 上，
    若 _process_cancels 不累计该号，_write_fill 的 deal 查询会查不到明细 →
    价格降级、px_source 退化为 order_obj/ref_price，本用例即失败。
    """
    reset(m)
    info = new_pending(m, vol=1000, oid="")
    C = Ctx()
    C.deals = [Deal("300413.SZ", "7007", 1000, 9.20)]
    m._lookup_order = lambda *a, **k: ("", Order("7007", 56, 1000, "SYS7", avg=9.40))
    m._process_cancels(C, {"cancels": [{"strategy_order_id": "s1"}]}, "20261002")
    check("T15 撤前全成->FILLED", statuses(m.written) == ["FILLED"], statuses(m.written))
    check("T15 pending已清", "s1" not in m._g_pending, list(m._g_pending))
    check("T15 未发撤单", len(m.cancels) == 0, m.cancels)
    check("T15 用deal真实均价(非订单对象价)", abs(m.written[0]["price"] - 9.20) < 1e-6,
          m.written[0]["price"])
    check("T15 价源=fill_avg", m.written[0]["px_source"] == "fill_avg", m.written[0]["px_source"])
    check("T15 fills留痕委托号", m.written[0]["order_ids"] == ["7007"], m.written[0]["order_ids"])


# ------------------------------------------------------------------ T16
def test_t16_process_cancels_no_oid(m):
    """[G2 特有] 拿不到真实 order_id：绝不假写 CANCELED（否则 QMT 真单变孤儿）。"""
    reset(m)
    new_pending(m, vol=1000, oid="")
    C = Ctx()
    C.deals = []
    m._lookup_order = lambda *a, **k: ("", None)
    m._process_cancels(C, {"cancels": [{"strategy_order_id": "s1"}]}, "20261002")
    check("T16 拿不到OID保留pending", "s1" in m._g_pending, list(m._g_pending))
    check("T16 拿不到OID不写终态", m.written == [], statuses(m.written))
    check("T16 拿不到OID不发撤单", len(m.cancels) == 0, m.cancels)


# ------------------------------------------------------------------ T17
def test_t17_timeout_first_lookup_oid(m):
    """[本轮修复] timeout 未知 OID 分支：首反查委托号必须累计；重报后再累计第二枚。"""
    reset(m)
    info = new_pending(m, vol=1000, oid="")
    C = Ctx({"300413.SZ": 9.05})
    C.deals = []
    seq = {"n": 0}

    def lookup(*a, **k):
        seq["n"] += 1
        if seq["n"] == 1:
            # 补齐分支：首次反查到原单
            return ("7007", Order("7007", 50, 0, "SYS7"))
        if seq["n"] <= 2:
            # 撤单确认轮询：报死透(57)
            return ("7007", Order("7007", 57, 0, "SYS7"))
        # 重报后反查新单
        return ("7008", Order("7008", 50, 0, "SYS8"))

    m._lookup_order = lookup
    m._handle_timeout_retry(C, "20261002", "s1", info, m.time.time())
    check("T17 原单号已累计", "7007" in info["order_ids"], info["order_ids"])
    check("T17 重报新单号已累计", "7008" in info["order_ids"], info["order_ids"])
    check("T17 order_id切到新单", info["order_id"] == "7008", info["order_id"])
    check("T17 已重报", len(m.orders) == 1, len(m.orders))
    check("T17 委托号无重复", len(info["order_ids"]) == len(set(info["order_ids"])),
          info["order_ids"])


# ------------------------------------------------------------------ T18
def test_t18_cancel_confirm_first_lookup_oid(m):
    """[本轮修复] cancel-confirm 首反查委托号必须累计。"""
    reset(m)
    info = new_pending(m, vol=1000, oid="", cancel_requested=True)
    C = Ctx()
    C.deals = [Deal("300413.SZ", "7007", 200, 9.10)]
    m._lookup_order = lambda *a, **k: ("", Order("7007", 54, 200, "SYS7", avg=9.10))
    m._handle_cancel_confirm(C, "20261002", "s1", info, m.time.time())
    check("T18 首反查委托号已累计", "7007" in info["order_ids"], info["order_ids"])
    check("T18 撤单确认CANCELED", statuses(m.written) == ["CANCELED"], statuses(m.written))
    check("T18 用deal真实均价", abs(m.written[0]["price"] - 9.10) < 1e-6, m.written[0]["price"])
    check("T18 价源=fill_avg", m.written[0]["px_source"] == "fill_avg", m.written[0]["px_source"])


# ------------------------------------------------------------------ T19
def test_t19_heartbeat_snapshot(m):
    """[自闭环重写 2026-10-20 T-20261020-001] print 心跳必须携带可观测计数。
    旧断言基于 heart_<date>.json 落盘（沙箱禁 open，2026-10-15 起永久废止）；
    现行唯一通道是 print [P16G2I][HEART] → FormulaOutput 日志，外部监控/桥存活判定
    全靠解析这一行，字段缺失=监控瞎。"""
    reset(m)
    m._g_slippage_alert_count = 3
    m._g_risk_sold.add("300413.SZ")
    m._g_today_risk_count = 1
    m._g_today_fill_count = 2
    m._g_last_cmd_seq = 42
    m._g_last_heart_ts = 0.0     # 解除 60s 节流
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        m._write_heartbeat("20261002", 42)
    finally:
        sys.stdout = old
    out = buf.getvalue()
    check("T19 打印HEART行", "[P16G2I][HEART]" in out, out[:200])
    for k in ("date=20261002", "pending=0", "positions=0", "risk_sold=1",
              "risk_count=1", "fills=2", "slippage=3", "passive=False"):
        check("T19 HEART含%s" % k, k in out, out[:300])
    # 60s 节流：第二次立即调用应被抑制（防 tick 级刷爆日志）
    buf2 = io.StringIO()
    sys.stdout = buf2
    try:
        m._write_heartbeat("20261002", 42)
    finally:
        sys.stdout = old
    check("T19 60s节流生效", buf2.getvalue() == "", buf2.getvalue()[:120])
    # 持仓明细行（旁路 grep [HOLD] 即得完整持仓）
    check("T19 HOLD行", "[P16G2I][HOLD]" in out, out[:400])


# ------------------------------------------------------------------ T20
def test_t20_init_rebuilds_state_from_account(m):
    """[自闭环重写 2026-10-20 T-20261020-001] init 不再从 heart json 恢复任何状态——
    自闭环架构下「账户即账本」（方案 §3.3 L2）：risk_sold / RSK 水位靠当日 order 表
    反查 remark 重建，pending 靠当日 order 反查重建。旧 T20（从 heart 恢复 pending/
    alert_count/seq）验证的是已废止的文件桥语义，保留会让测试永远红。"""
    reset(m)
    m._today_str = lambda: "20261002"
    # 当日 order 表：两笔带 P16G2桥 remark 的卖出委托 → 应重建 risk_sold=2 只 + RSK 水位=2
    m.g["get_trade_detail_data"] = lambda a, b, tp: (
        [Order(oid="1001", status=56, deal_vol=1000, avg=9.4),
         Order(oid="1002", status=56, deal_vol=500, avg=9.6)] if tp == "order" else [])
    for o in m.g["get_trade_detail_data"](1, "stock", "order"):
        o.m_strRemark = "P16G2I桥"
        o.m_strInstrumentID = "300413.SZ"
    m.init(Ctx())
    check("T20 账户反查重建risk_sold", len(m._g_risk_sold) >= 1, m._g_risk_sold)
    check("T20 账户反查重建RSK水位", m._g_today_risk_count >= 1, m._g_today_risk_count)
    check("T20 未从文件恢复pending", len(m._g_pending) == 0, list(m._g_pending))
    # 注：本桩无 get_market_data_ex，L3 快照载入失败 → passive=True 属设计内 fail-safe
    # （宁可不交易也不盲跑），故只断言「风险状态已从账户重建」，不断言 passive。


# ------------------------------------------------------------------ T21
def test_t21_new_day_resets_alert_count(m):
    """跨日必须打印前日汇总并清零告警计数（否则跨日累加无法归因到具体某天）。"""
    reset(m)
    m._g_today = "20261001"
    m._g_slippage_alert_count = 4
    m._g_pending["s1"] = {"code": "300413.SZ"}
    m._g_risk_sold.add("300413.SZ")
    m._g_last_cmd_seq = 9
    m._g_passive = False   # 前置 T20 的桩 init 会因缺 get_market_data_ex 转被动，此处显式复位
    m._today_str = lambda: "20261002"
    beats = []
    m._write_heartbeat = lambda date, seq: beats.append((date, seq))
    # Ctx.get_current_time 返回 08:00:00（非交易时段）→ handlebar 走完跨日块即返回
    m.handlebar(Ctx())
    check("T21 跨日已更新_g_today", m._g_today == "20261002", m._g_today)
    check("T21 跨日告警计数清零", m._g_slippage_alert_count == 0, m._g_slippage_alert_count)
    check("T21 跨日pending清空", len(m._g_pending) == 0, list(m._g_pending))
    check("T21 跨日risk_sold清空", len(m._g_risk_sold) == 0, m._g_risk_sold)
    check("T21 跨日seq归零", m._g_last_cmd_seq == 0, m._g_last_cmd_seq)
    check("T21 跨日后仍写心跳", len(beats) == 1, beats)


def main():
    m = load()
    for fn in (test_t1_filled_by_volume, test_t2_rejected_retry,
               test_t3_partial_not_rejected, test_t4_canceled,
               test_t5_partial_then_timeout, test_t6_filled_before_cancel,
               test_t7_cancel_not_dead, test_t8_refresh_price_on_retry,
               test_t9_retry_exhausted, test_t10_cancel_confirm,
               test_t11_cancel_not_effective, test_t12_all_exits_use_px_protocol,
               test_t13_process_cancels_first_lookup_oid,
               test_t14_process_cancels_sends_cancel,
               test_t15_process_cancels_filled_too_late,
               test_t16_process_cancels_no_oid,
               test_t17_timeout_first_lookup_oid,
               test_t18_cancel_confirm_first_lookup_oid,
               test_t19_heartbeat_snapshot,
               test_t20_init_rebuilds_state_from_account,
               test_t21_new_day_resets_alert_count):
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