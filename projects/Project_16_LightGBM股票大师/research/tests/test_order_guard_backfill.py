# -*- coding: utf-8 -*-
"""order_guard 撤单窗口成交补记 + 活跃态双重委托防护 回归测试（T-20260930-001）。

背景（2026-09-30 603237.SH 事故）：卖出 2800 股在撤单竞争窗口内全部成交，但
order_guard 返回 ok=False / traded_vol=0（CANCELED_TIMEOUT），rebalance_daily 的
记账门槛 `ok and traded_vol>0` 双重条件把真实成交挡在账本外 → 账实脱节。
根因三层（本测试逐一覆盖）：
  1) 撤单失败死透确认窗口的成交从未补记（原 `pass`）；
  2) 活跃态(50/51/52)超时落入底部 continue 盲重挂 = 双重委托风险；
  3) 记账门槛 `ok and traded_vol` 把 ok=False 但有真实成交的结果漏记账本。

用例（mock trader，不连真 QMT / 不产生真实委托）：
  S1 事故复现：超时(50 活跃) → 撤单失败 → 死透确认发现窗口内全成 2800
     → 必须返回 FILLED / traded_vol=2800（修复前 ok=False / traded_vol=0）
  S2 撤单成功窗口补记：超时部成 1000 → 撤单受理窗口又成 800 → 重挂必须只挂净剩余 1000（防双倍）
  S3 重试耗尽部分成交：ok=False 但 traded_vol=1000 —— 配合源码断言保证必入账本
  S4 活跃态防重挂：撤单失败且原单仍活跃 → 绝不重挂（修复前盲重挂直至耗尽重试）
  S5 记账门槛源码断言：生产代码不再存在 `ok and traded_vol` 双重门槛

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_order_guard_backfill.py
"""
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

# ---- 屏蔽真 xtquant（防测试连真 miniQMT 客户端/真实行情），提供最小 stub ----
# qmt_config 顶层只 import stdlib（已核实），先 stub 再 import order_guard 安全。
_xtquant = types.ModuleType("xtquant")
_xtconstant = types.ModuleType("xtquant.xtconstant")
_xtconstant.STOCK_BUY = 23
_xtconstant.STOCK_SELL = 24
_xtconstant.FIX_PRICE = 11
_xtconstant.LATEST_PRICE = 5
_xtquant.xtconstant = _xtconstant
sys.modules.setdefault("xtquant", _xtquant)
sys.modules.setdefault("xtquant.xtconstant", _xtconstant)
# 注意：故意不提供 xtquant.xtdata —— get_limit_prices/_fetch_price 会走 except 分支
# 返回 None（无涨跌停数据不跳过 / 取价失败回退传入价），与「客户端未启动」降级路径一致。

import time  # noqa: E402

import order_guard as G  # noqa: E402

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


class _Order(object):
    """模拟 xtquant 委托对象（getattr 取属性）。"""

    def __init__(self, oid, status, traded, vol):
        self.order_id = oid
        self.order_status = status
        self.traded_volume = traded
        self.order_volume = vol


class FakeTrader(object):
    """模拟 miniQMT XtQuantTrader 最小接口。

    - order_stock：登记 oid，初始状态 pending_state；reject_from 第 N 笔起返回 -1
      （模拟无券可卖拒单）；auto_fill_from 第 N 笔起下单即全部成交(56)
    - cancel_order_stock：按撤单调用序号取 cancel_script 的 (返回码, 撤单后原单新状态)
      并原子切换——撤单调用即视为交易所处理完毕，用于模拟「撤单竞争窗口内成交」
    - query_stock_orders：返回各单状态快照；query_transitions 按查询调用序号切状态，
      用于模拟「等待窗口内成交」
    """

    def __init__(self, cancel_script=None, query_transitions=None,
                 auto_fill_from=None, reject_from=None):
        self.cancel_script = cancel_script or {}
        self.query_transitions = query_transitions or []
        self.auto_fill_from = auto_fill_from
        self.reject_from = reject_from
        self.placements = []            # [(oid, vol)]
        self.state = {}                 # oid -> [status, traded, vol]
        self._next_oid = 1001
        self._cancel_calls = 0
        self._query_calls = 0

    def order_stock(self, account, code, order_type, vol, price_type, px, strategy, remark):
        seq = len(self.placements) + 1
        if self.reject_from is not None and seq >= self.reject_from:
            return -1
        oid = self._next_oid
        self._next_oid += 1
        self.placements.append((oid, vol))
        if self.auto_fill_from is not None and seq >= self.auto_fill_from:
            self.state[oid] = [56, vol, vol]
        else:
            self.state[oid] = [50, 0, vol]
        return oid

    def cancel_order_stock(self, account, oid):
        self._cancel_calls += 1
        ret, new_state = self.cancel_script[self._cancel_calls]
        self.state[oid] = list(new_state)
        return ret

    def query_stock_orders(self, account):
        self._query_calls += 1
        for n, new_state in self.query_transitions:
            if n == self._query_calls:
                oid = self.placements[-1][0]
                self.state[oid] = list(new_state)
        return [_Order(oid, s[0], s[1], s[2]) for oid, s in self.state.items()]


def _run_guard(trader, vol=2800, price=15.24):
    return G.order_with_guard(trader, None, "603237.SH", "SELL", vol, price, "test_guard")


def test_s1_incident_cancel_fail_window_filled():
    """S1 事故复现：撤单失败 → 死透确认发现窗口内全部成交 → 必须返回 FILLED。"""
    t = FakeTrader(cancel_script={1: (-1, (54, 2800, 2800))})
    r = _run_guard(t)
    _check("S1 返回 FILLED（修复前 CANCELED_TIMEOUT）", r["action"] == "FILLED", r)
    _check("S1 ok=True", r["ok"] is True, r["ok"])
    _check("S1 traded_vol=2800（修复前 0 → 漏记账本）", r["traded_vol"] == 2800, r["traded_vol"])
    _check("S1 只下过 1 笔委托（无双重委托）", len(t.placements) == 1, t.placements)


def test_s2_cancel_ok_window_backfill_exact_replacement():
    """S2 撤单受理窗口补记：等待期成 1000 + 撤单窗口再成 800 → 重挂只挂净剩余 1000。"""
    t = FakeTrader(
        query_transitions=[(1, (52, 1000, 2800))],   # 等待窗口内先成 1000
        cancel_script={1: (0, (54, 1800, 2800))},    # 撤单受理，处理窗口内又成 800
        auto_fill_from=2)                            # 第 2 笔重挂即全部成交
    r = _run_guard(t)
    _check("S2 返回 FILLED", r["action"] == "FILLED", r)
    _check("S2 traded_vol=2800（1000+800+1000）", r["traded_vol"] == 2800, r["traded_vol"])
    _check("S2 重挂只挂净剩余 1000（防双倍：修复前按 2800 或 1800 重挂）",
           t.placements == [(1001, 2800), (1002, 1000)], t.placements)


def test_s3_exhausted_partial_traded_vol_kept():
    """S3 重试耗尽部分成交：ok=False 但 traded_vol=1000 —— 记账门槛必须只看 traded_vol。"""
    G.MAX_RETRY = 3
    try:
        t = FakeTrader(
            query_transitions=[(1, (52, 1000, 2800))],
            cancel_script={1: (0, (54, 1000, 2800))},   # 撤单处理窗口无新增成交
            reject_from=2)                               # 重挂被拒（无券可卖，事故同款）
        r = _run_guard(t)
        _check("S3 返回 CANCELED_TIMEOUT", r["action"] == "CANCELED_TIMEOUT", r)
        _check("S3 ok=False", r["ok"] is False, r["ok"])
        _check("S3 traded_vol=1000 不丢失（修复前该 1000 股漏记账本）",
               r["traded_vol"] == 1000, r["traded_vol"])
    finally:
        G.MAX_RETRY = 10


def test_s4_active_order_never_replaced():
    """S4 活跃态防重挂：撤单失败且原单仍活跃 → 放弃重挂（修复前盲重挂 = 双重委托）。"""
    t = FakeTrader(cancel_script={1: (-1, (50, 0, 2800))})   # 撤单失败，原单仍活跃
    r = _run_guard(t)
    _check("S4 traded_vol=0", r["traded_vol"] == 0, r["traded_vol"])
    _check("S4 action 为 CANCELED_TIMEOUT/ERROR", r["action"] in ("CANCELED_TIMEOUT", "ERROR"), r["action"])
    _check("S4 绝不重挂（修复前盲重挂直至 MAX_RETRY 耗尽）",
           len(t.placements) == 1, t.placements)


def test_s5_booking_gate_source_assertions():
    """S5 记账门槛源码断言：生产代码禁止 `ok and traded_vol` 双重门槛（T-20260930-001）。"""
    files = {
        "rebalance_daily.py": 3,   # 卖出 / 减仓 / 买入 三处
        "qmt_clear.py": 1,
        "qmt_monitor.py": None,    # monitor 本就正确（traded_vol>0 记账，ok 只管 sold.add）
    }
    for fname, expect in files.items():
        path = os.path.join(PROJECT_ROOT, fname)
        with open(path, encoding="utf-8") as f:
            src = f.read()
        _check("S5 %s 无 `ok and traded_vol` 双重门槛" % fname,
               'r["ok"] and r["traded_vol"]' not in src, fname)
        if expect is not None:
            _check("S5 %s 记账门槛 `traded_vol > 0` 共 %d 处" % (fname, expect),
                   src.count('if r["traded_vol"] > 0:') == expect,
                   src.count('if r["traded_vol"] > 0:'))
        else:
            _check("S5 %s 记账门槛存在" % fname,
                   'if r["traded_vol"] > 0:' in src, fname)


if __name__ == "__main__":
    print("== order_guard 撤单窗口成交补记 + 双重委托防护 回归测试（T-20260930-001）==")
    _real_sleep = time.sleep
    _real_defaults = G.wait_order_fill.__defaults__
    _real_poll = G.POLL_INTERVAL
    try:
        # 提速：单轮等待 1s、轮询间隔/守护 sleep 压到 20ms（不改变逻辑分支）
        G.wait_order_fill.__defaults__ = (1,)
        G.POLL_INTERVAL = 0.05
        time.sleep = lambda s: _real_sleep(min(s, 0.02))
        test_s1_incident_cancel_fail_window_filled()
        test_s2_cancel_ok_window_backfill_exact_replacement()
        test_s3_exhausted_partial_traded_vol_kept()
        test_s4_active_order_never_replaced()
        test_s5_booking_gate_source_assertions()
    finally:
        time.sleep = _real_sleep
        G.wait_order_fill.__defaults__ = _real_defaults
        G.POLL_INTERVAL = _real_poll
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
