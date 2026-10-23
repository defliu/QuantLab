# -*- coding: utf-8 -*-
"""G2 大QMT 文件桥「deal 成交均价 + 跳空/滑点」纯函数测试。

与 V1.3 同口径（同一套降级协议与阈值），本文件只针对 G2 源码
strategy/strategy_p16_g2_selfcontained_src.py 的实现做回归：

  G1. _query_deals 三态：接口缺失 / 无委托号 / 正常过滤
  G2. _resolve_fill_avg：加权均价、覆盖校验、多委托号、写入延迟重试
  G3. _resolve_px 降级链：fill_avg → order_obj → ref_price → unresolved
  G4. _adverse_slippage：买/卖方向、缺基准不臆造
  G5. _gap_pct：跳空破位幅度、无线/未破位返回 0
  G6. _slippage_alert：只告警不拦单 + 计数；格式化不得重复放大
  G7. _acc_order_id：多轮委托号去重累计
  G8. _refresh_last_price：现价兜底不回落为 0
  G9. _write_fill：fills 记录必须带完整成交价协议字段
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


class Deal(object):
    def __init__(self, code, oid, vol, px, has_oid=True):
        self.m_strInstrumentID = code
        self.m_strStockCode = code
        self.m_nVolume = vol
        self.m_nVolumeTotal = vol
        self.m_dAvgPrice = px
        self.m_dPrice = px
        if has_oid:
            self.m_nOrderID = oid
            self.m_nOrderSysID = oid


class Order(object):
    def __init__(self, oid="", status=50, deal_vol=0, sysid="", avg=0.0, px=0.0):
        self.m_nOrderID = oid
        self.m_nOrderStatus = status
        self.m_nDealVolume = deal_vol
        self.m_strSysid = sysid
        if avg:
            self.m_dAvgPrice = avg
        if px:
            self.m_dPrice = px


class Ctx(object):
    def __init__(self, ticks=None, deals=None, no_api=False):
        self._ticks = ticks or {}
        self.deals = deals if deals is not None else []
        self._no_api = no_api
        self.deal_calls = 0

    def get_full_tick(self, codes):
        return {c: {"lastPrice": v} for c, v in self._ticks.items()}

    def get_trade_detail_data(self, acct, mkt, tp):
        if self._no_api:
            raise AttributeError("no get_trade_detail_data")
        self.deal_calls += 1
        return self.deals


def load():
    with io.open(SRC, "r", encoding="utf-8") as f:
        src = f.read()
    mod = types.ModuleType("g2_dfs_under_test")
    mod.__file__ = SRC
    exec(compile(src, SRC, "exec"), mod.__dict__)
    mod.__dict__["time"] = FakeTime()
    return mod


def reset(m):
    m._g_deal_ok = None
    m._g_deal_err_count = 0
    m._g_deal_no_oid = False
    m._g_slippage_alert_count = 0
    m.written = []
    # 拦截统一出口：本组测试只验协议字段，不落盘。
    # 自闭环（2026-10-20 T-20261020-001）：_write_fill 内部同时 print [P16G2I][FILL] 并调
    # _add_or_update_fill；此处只拦落盘入口，print 照常（正是新增的可观测通道）。
    m._add_or_update_fill = lambda date, rec: m.written.append(rec)


# ------------------------------------------------------------------ G1
def test_g1_query_deals_three_states(m):
    # G1a 接口完全不存在（无该方法）-> 立即永久降级，绝不抛异常
    reset(m)

    class NoApiCtx(object):
        def get_full_tick(self, codes):
            return {}

    check("G1a 接口缺失返回空", m._query_deals(NoApiCtx(), "300413.SZ", ["1001"]) == [])
    check("G1a 标记不可用", m._g_deal_ok is False, m._g_deal_ok)

    # G1a2 抛异常属抖动：单次不永久降级，累计 DEAL_QUERY_MAX_ERR 次才熔断
    reset(m)
    C = Ctx(no_api=True)
    m._query_deals(C, "300413.SZ", ["1001"])
    check("G1a2 单次异常不熔断", m._g_deal_ok is None, m._g_deal_ok)
    check("G1a2 异常计数+1", m._g_deal_err_count == 1, m._g_deal_err_count)
    m._query_deals(C, "300413.SZ", ["1001"])
    check("G1a2 连续异常仍不熔断", m._g_deal_ok is None, m._g_deal_ok)
    m._query_deals(C, "300413.SZ", ["1001"])
    check("G1a2 达上限才熔断", m._g_deal_ok is False, m._g_deal_ok)

    # G1b 全无委托号 -> 拒绝归属本次委托（fail-safe），避免混入历史成交
    reset(m)
    C = Ctx(deals=[Deal("300413.SZ", "", 1000, 9.4, has_oid=False)])
    check("G1b 无委托号拒绝", m._query_deals(C, "300413.SZ", ["1001"]) == [])
    check("G1b 告警只打一次", m._g_deal_no_oid is True, m._g_deal_no_oid)

    # G1c 正常过滤：只取 stock_code + order_id 双重匹配的明细
    reset(m)
    C = Ctx(deals=[
        Deal("300413.SZ", "1001", 400, 9.40),
        Deal("300413.SZ", "1001", 600, 9.60),
        Deal("300415.SZ", "1001", 1000, 99.0),
        Deal("300413.SZ", "9999", 1000, 88.0),
    ])
    rows = m._query_deals(C, "300413.SZ", ["1001"])
    tot = sum(r[1] for r in rows)
    check("G1c 仅本次委托本次股票", tot == 1000, rows)
    check("G1c 剔除他股成交", all(abs(r[2] - 99.0) > 1e-9 for r in rows), rows)
    check("G1c 剔除他单成交", all(abs(r[2] - 88.0) > 1e-9 for r in rows), rows)


# ------------------------------------------------------------------ G2
def test_g2_resolve_fill_avg(m):
    # G2a 加权均价：Σ(量×价)/Σ量
    reset(m)
    C = Ctx(deals=[Deal("300413.SZ", "1001", 400, 9.40),
                   Deal("300413.SZ", "1002", 600, 9.60)])
    avg = m._resolve_fill_avg(C, "300413.SZ", ["1001", "1002"], 1000)
    check("G2a 加权均价", abs(avg - (400 * 9.40 + 600 * 9.60) / 1000.0) < 1e-9, avg)

    # G2b 明细量不足 expect_vol -> 降级返回 0（绝不用部分均价冒充全部真实均价）
    reset(m)
    C = Ctx(deals=[Deal("300413.SZ", "1001", 400, 9.40)])
    check("G2b 量不足降级", m._resolve_fill_avg(C, "300413.SZ", ["1001"], 1000) == 0.0)

    # G2c 无委托号 -> 不查
    reset(m)
    C = Ctx(deals=[Deal("300413.SZ", "1001", 1000, 9.4)])
    check("G2c 无oid不查", m._resolve_fill_avg(C, "300413.SZ", [], 1000) == 0.0)
    check("G2c 无oid未调用接口", C.deal_calls == 0, C.deal_calls)

    # G2d 明细写入延迟：第一次查不到，重试后出现 -> 必须拿到真实均价
    reset(m)
    calls = {"n": 0}
    C = Ctx(deals=[])

    def delayed(acct, mkt, tp):
        calls["n"] += 1
        if calls["n"] == 1:
            return []
        return [Deal("300413.SZ", "1001", 1000, 9.35)]

    C.get_trade_detail_data = delayed
    avg = m._resolve_fill_avg(C, "300413.SZ", ["1001"], 1000)
    check("G2d 延迟重试拿到均价", abs(avg - 9.35) < 1e-9, avg)


# ------------------------------------------------------------------ G3
def test_g3_resolve_px_chain(m):
    # G3a deal 可用 -> fill_avg 优先
    reset(m)
    C = Ctx(deals=[Deal("300413.SZ", "1001", 1000, 9.30)])
    px, src, adv = m._resolve_px(C, "300413.SZ", "SELL", ["1001"], 1000,
                                  Order("1001", 56, 1000, "S1", avg=9.50), 9.50)
    check("G3a 优先fill_avg", src == "fill_avg", src)
    check("G3a 价格取真实均价", abs(px - 9.30) < 1e-9, px)

    # G3b deal 不可用 -> 降级 order_obj
    reset(m)
    C = Ctx(deals=[])
    px, src, adv = m._resolve_px(C, "300413.SZ", "SELL", ["1001"], 1000,
                                  Order("1001", 56, 1000, "S1", avg=9.50), 9.50)
    check("G3b 降级order_obj", src == "order_obj", src)
    check("G3b 取订单对象价", abs(px - 9.50) < 1e-9, px)

    # G3c order_obj 也无价 -> ref_price
    reset(m)
    C = Ctx(deals=[])
    px, src, adv = m._resolve_px(C, "300413.SZ", "SELL", ["1001"], 1000,
                                  Order("1001", 50, 0, "S1"), 9.50)
    check("G3c 降级ref_price", src == "ref_price", src)
    check("G3c 无基准不算滑点", adv == 0.0, adv)

    # G3d 全部无正价 -> unresolved（显式标记，绝不伪造 0 价成交）
    reset(m)
    C = Ctx(deals=[])
    px, src, adv = m._resolve_px(C, "300413.SZ", "SELL", [], 0, None, 0)
    check("G3d 标记unresolved", src == "unresolved", src)
    check("G3d 不返回正价", px == 0.0, px)


# ------------------------------------------------------------------ G4
def test_g4_adverse_slippage(m):
    check("G4 卖出看卖低", abs(m._adverse_slippage("SELL", 10.0, 9.5) - 0.05) < 1e-9)
    check("G4 买入看买高", abs(m._adverse_slippage("BUY", 10.0, 10.5) - 0.05) < 1e-9)
    check("G4 卖出价更高为有利=负", m._adverse_slippage("SELL", 10.0, 10.5) < 0)
    check("G4 买入价更低为有利=负", m._adverse_slippage("BUY", 10.0, 9.5) < 0)
    check("G4 无基准不臆造", m._adverse_slippage("SELL", 0, 9.5) == 0.0)
    check("G4 无成交价不臆造", m._adverse_slippage("SELL", 10.0, 0) == 0.0)


# ------------------------------------------------------------------ G5
def test_g5_gap_pct(m):
    # 成本 10，止损线 9.30；触发价 9.00 -> 破位 3%
    check("G5 跳空破位幅度",
          abs(m._gap_pct(10.0, 9.00) - 0.03) < 1e-9, m._gap_pct(10.0, 9.00))
    # 未跌破止损线 -> 不算跳空
    check("G5 未破位返回0", m._gap_pct(10.0, 9.50) == 0.0)
    check("G5 恰好在线上返回0", m._gap_pct(10.0, 9.30) == 0.0)
    check("G5 无成本不判定", m._gap_pct(0, 9.0) == 0.0)
    check("G5 无触发价不判定", m._gap_pct(10.0, 0) == 0.0)


# ------------------------------------------------------------------ G6
def test_g6_slippage_alert(m):
    # 返回契约：命中时返回可写入 fills 的机器可读尾标（adverse_slippage=/gap_exceeded=），
    # 未命中返回空串；中文描述只进日志，不进 fills（fills 要给下游解析用）。
    reset(m)
    tail = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.5, 0.05, 0.0)
    check("G6 滑点超限有尾标", "adverse_slippage=5.00%" in tail, tail)
    check("G6 滑点告警计数", m._g_slippage_alert_count == 1, m._g_slippage_alert_count)
    # 百分比格式化不得重复放大（T-20261002 修复过的坑）：5% 只应出现一次 5.00%
    check("G6 百分比未放大", tail.count("5.00%") == 1, tail)

    reset(m)
    tail = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.0, 0.0, 0.05)
    check("G6 跳空超限有尾标", "gap_exceeded=5.00%" in tail, tail)
    check("G6 跳空告警计数", m._g_slippage_alert_count == 1, m._g_slippage_alert_count)

    reset(m)
    tail = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.98, 0.002, 0.001)
    check("G6 未超限无尾标", tail == "", tail)
    check("G6 未超限不计数", m._g_slippage_alert_count == 0, m._g_slippage_alert_count)

    reset(m)
    tail = m._slippage_alert("s1", "300413.SZ", "SELL", "fill_avg", 10.0, 9.5, 0.05, 0.05)
    check("G6 双超限合并一条尾标", tail.count("=") == 2, tail)
    check("G6 双超限只计一次", m._g_slippage_alert_count == 1, m._g_slippage_alert_count)


# ------------------------------------------------------------------ G7
def test_g7_acc_order_id(m):
    info = {}
    m._acc_order_id(info, "1001")
    m._acc_order_id(info, "1002")
    m._acc_order_id(info, "1001")
    m._acc_order_id(info, "")
    m._acc_order_id(info, None)
    check("G7 去重累计", info["order_ids"] == ["1001", "1002"], info["order_ids"])


# ------------------------------------------------------------------ G8
def test_g8_refresh_last_price(m):
    C = Ctx({"300413.SZ": 9.77})
    check("G8 取现价", abs(m._refresh_last_price(C, "300413.SZ", 5.0) - 9.77) < 1e-9)
    # 现价取不到 -> 回落 fallback，绝不返回 0（0 价会让市价重挂报出 0 元单）
    C2 = Ctx({})
    v = m._refresh_last_price(C2, "300413.SZ", 5.0)
    check("G8 无行情回落fallback", abs(v - 5.0) < 1e-9, v)


# ------------------------------------------------------------------ G9
def test_g9_write_fill_protocol(m):
    reset(m)
    m._g_pending.clear()
    # 止损跳空 + 实际滑点双超限场景：
    #   成本 10.00 -> 止损线 9.30
    #   触发价 9.00 已在线下 = 跳空 3.00% > MAX_GAP_TOLERANCE_PCT(2%)
    #   成交均价 8.70 -> 不利滑点 (9.00-8.70)/9.00 = 3.33% > MAX_ADVERSE_SLIPPAGE_PCT(3%)
    info = {
        "code": "300413.SZ", "bridge_code": "300413.SZ", "action": "SELL",
        "vol": 1000, "cur_vol": 1000, "filled_so_far": 0,
        "price": 9.00, "trigger_price": 9.00, "cost_price": 10.00,
        "last_price": 9.00, "retry": 0, "order_id": "1001",
        "order_ids": ["1001"], "sysid": "SYS1", "status": "PENDING",
        "risk_reason": "stop_loss",
    }
    C = Ctx(deals=[Deal("300413.SZ", "1001", 1000, 8.70)])
    rec = m._write_fill(C, "20261002", "s1", info, "300413.SZ", "SELL",
                        1000, "FILLED", "SYS1", "stop_loss")
    # 落盘统一 round(..., 6)，断言容差必须 >= 1e-6
    for k in ("px_source", "trigger_price", "adverse_slippage", "gap_pct",
              "order_ids", "retry"):
        check("G9 fills含%s" % k, k in rec, list(rec.keys()))
    check("G9 用真实成交均价", abs(rec["price"] - 8.70) < 1e-6, rec["price"])
    check("G9 价源fill_avg", rec["px_source"] == "fill_avg", rec["px_source"])
    check("G9 不利滑点方向正确(卖低)",
          abs(rec["adverse_slippage"] - round((9.00 - 8.70) / 9.00, 6)) < 1e-6,
          rec["adverse_slippage"])
    check("G9 跳空已留痕", abs(rec["gap_pct"] - round(0.30 / 10.00, 6)) < 1e-6,
          rec["gap_pct"])
    check("G9 累计多轮委托号", rec["order_ids"] == ["1001"], rec["order_ids"])
    check("G9 走了统一出口", len(m.written) == 1, m.written)
    check("G9 滑点告警并入reason", "adverse_slippage=" in rec["reason"], rec["reason"])
    check("G9 跳空告警并入reason", "gap_exceeded=" in rec["reason"], rec["reason"])
    check("G9 双超限只计一次告警", m._g_slippage_alert_count == 1,
          m._g_slippage_alert_count)


def main():
    m = load()
    for fn in (test_g1_query_deals_three_states,
               test_g2_resolve_fill_avg,
               test_g3_resolve_px_chain,
               test_g4_adverse_slippage,
               test_g5_gap_pct,
               test_g6_slippage_alert,
               test_g7_acc_order_id,
               test_g8_refresh_last_price,
               test_g9_write_fill_protocol):
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