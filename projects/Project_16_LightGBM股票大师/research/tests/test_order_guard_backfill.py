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
import json  # noqa: E402
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
import qmt_config as C  # noqa: E402

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


class _Trade(object):
    """模拟 xtquant 成交明细对象（MINIQMT_GUIDE §6.3 字段）。"""

    def __init__(self, oid, code, vol, price):
        self.order_id = oid
        self.stock_code = code
        self.traded_volume = vol
        self.traded_price = price
        self.traded_time = 0


class FakeTrader(object):
    """模拟 miniQMT XtQuantTrader 最小接口。

    - order_stock：登记 oid，初始状态 pending_state；reject_from 第 N 笔起返回 -1
      （模拟无券可卖拒单）；auto_fill_from 第 N 笔起下单即全部成交(56)
    - cancel_order_stock：按撤单调用序号取 cancel_script 的 (返回码, 撤单后原单新状态)
      并原子切换——撤单调用即视为交易所处理完毕，用于模拟「撤单竞争窗口内成交」
    - query_stock_orders：返回各单状态快照；query_transitions 按查询调用序号切状态，
      用于模拟「等待窗口内成交」
    - query_stock_trades（T-20261002-001）：返回 fills 指定的成交明细；
      fills=None 时抛异常模拟「成交明细不可用/非交易时段阻塞」
    """

    def __init__(self, cancel_script=None, query_transitions=None,
                 auto_fill_from=None, reject_from=None, fills=None):
        self.cancel_script = cancel_script or {}
        self.query_transitions = query_transitions or []
        self.auto_fill_from = auto_fill_from
        self.reject_from = reject_from
        self.fills = fills
        self.placements = []            # [(oid, vol)]
        self.state = {}                 # oid -> [status, traded, vol]
        self._next_oid = 1001
        self._cancel_calls = 0
        self._query_calls = 0
        self.trade_queries = 0          # 统计 query_stock_trades 调用次数

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

    def query_stock_trades(self, account):
        self.trade_queries += 1
        if self.fills is None:
            raise RuntimeError("query_stock_trades unavailable (mock)")
        return [_Trade(oid, code, vol, px) for oid, code, vol, px in self.fills]


def _run_guard(trader, vol=2800, price=15.24):
    return G.order_with_guard(trader, None, "603237.SH", "SELL", vol, price, "test_guard")


def test_s1_incident_cancel_fail_window_filled():
    """S1 事故复现：撤单失败 → 死透确认发现窗口内全部成交 → 必须返回 FILLED。"""
    t = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=[(1001, "603237.SH", 2800, 15.31)],
    )
    r = _run_guard(t)
    _check("S1 返回 FILLED（修复前 CANCELED_TIMEOUT）", r["action"] == "FILLED", r)
    _check("S1 ok=True", r["ok"] is True, r["ok"])
    _check("S1 traded_vol=2800（修复前 0 → 漏记账本）", r["traded_vol"] == 2800, r["traded_vol"])
    _check("S1 只下过 1 笔委托（无双重委托）", len(t.placements) == 1, t.placements)
    _check("S1 avg_price=15.31（真实成交均价，非触发价 15.24）",
           r["avg_price"] == 15.31, r["avg_price"])


def test_s2_cancel_ok_window_backfill_exact_replacement():
    """S2 撤单受理窗口补记：等待期成 1000 + 撤单窗口再成 800 → 重挂只挂净剩余 1000。"""
    t = FakeTrader(
        query_transitions=[(1, (52, 1000, 2800))],   # 等待窗口内先成 1000
        cancel_script={1: (0, (54, 1800, 2800))},    # 撤单受理，处理窗口内又成 800
        auto_fill_from=2,                            # 第 2 笔重挂即全部成交
        # 两个 order_id 的成交明细必须一起汇总（1001 成 1800 分两笔、1002 成 1000）
        fills=[
            (1001, "603237.SH", 1000, 15.10),
            (1001, "603237.SH", 800, 15.26),
            (1002, "603237.SH", 1000, 15.33),
        ],
    )
    r = _run_guard(t)
    _check("S2 返回 FILLED", r["action"] == "FILLED", r)
    _check("S2 traded_vol=2800（1000+800+1000）", r["traded_vol"] == 2800, r["traded_vol"])
    _check("S2 重挂只挂净剩余 1000（防双倍：修复前按 2800 或 1800 重挂）",
           t.placements == [(1001, 2800), (1002, 1000)], t.placements)
    # 加权均价 = (1000*15.10 + 800*15.26 + 1000*15.33) / 2800
    _check("S2 avg_price=15.236（跨 order_id 加权，非单一委托价）",
           r["avg_price"] == round((1000 * 15.10 + 800 * 15.26 + 1000 * 15.33) / 2800, 3),
           r["avg_price"])


def test_s3_exhausted_partial_traded_vol_kept():
    """S3 重试耗尽部分成交：ok=False 但 traded_vol=1000 —— 记账门槛必须只看 traded_vol。"""
    G.MAX_RETRY = 3
    try:
        t = FakeTrader(
            query_transitions=[(1, (52, 1000, 2800))],
            cancel_script={1: (0, (54, 1000, 2800))},   # 撤单处理窗口无新增成交
            reject_from=2,                               # 重挂被拒（无券可卖，事故同款）
            fills=[(1001, "603237.SH", 1000, 14.88)],
        )
        r = _run_guard(t)
        _check("S3 返回 CANCELED_TIMEOUT", r["action"] == "CANCELED_TIMEOUT", r)
        _check("S3 ok=False", r["ok"] is False, r["ok"])
        _check("S3 traded_vol=1000 不丢失（修复前该 1000 股漏记账本）",
               r["traded_vol"] == 1000, r["traded_vol"])
        _check("S3 ok=False 仍能回填 avg_price=14.88（部分成交的真实均价）",
               r["avg_price"] == 14.88, r["avg_price"])
    finally:
        G.MAX_RETRY = 10


def test_s4_active_order_never_replaced():
    """S4 活跃态防重挂：撤单失败且原单仍活跃 → 放弃重挂（修复前盲重挂 = 双重委托）。"""
    t = FakeTrader(
        cancel_script={1: (-1, (50, 0, 2800))},   # 撤单失败，原单仍活跃
        fills=[],                                  # 零成交 → 不应触发均价查询
    )
    r = _run_guard(t)
    _check("S4 traded_vol=0", r["traded_vol"] == 0, r["traded_vol"])
    _check("S4 action 为 CANCELED_TIMEOUT/ERROR", r["action"] in ("CANCELED_TIMEOUT", "ERROR"), r["action"])
    _check("S4 绝不重挂（修复前盲重挂直至 MAX_RETRY 耗尽）",
           len(t.placements) == 1, t.placements)
    _check("S4 零成交 avg_price 必须为 None（无成交谈价格）",
           r["avg_price"] is None, r["avg_price"])
    _check("S4 零成交不查成交明细（避免非交易时段阻塞）",
           t.trade_queries == 0, t.trade_queries)


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


# ======================================================================
# T-20261002-001：真实成交均价（avg_price）+ 待补价队列 专属用例
# ======================================================================


class _SeqFillTrader(FakeTrader):
    """query_stock_trades 按调用序号返回不同明细，模拟「成交明细晚于委托状态同步」。"""

    def __init__(self, fill_seq=None, **kw):
        FakeTrader.__init__(self, **kw)
        self.fill_seq = fill_seq or []

    def query_stock_trades(self, account):
        self.trade_queries += 1
        n = self.trade_queries
        for k, fills in self.fill_seq:
            if n == k:
                if fills is None:
                    raise RuntimeError("not synced yet")
                return [_Trade(oid, code, vol, px) for oid, code, vol, px in fills]
        if self.fills is None:
            raise RuntimeError("query_stock_trades unavailable (mock)")
        return [_Trade(oid, code, vol, px) for oid, code, vol, px in self.fills]


def test_a1_single_order_multi_trades_weighted():
    """A1 同一 order_id 多笔成交 → 必须加权，不能取第一笔或最后一笔。"""
    t = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=[
            (1001, "603237.SH", 1000, 15.10),
            (1001, "603237.SH", 800, 15.26),
            (1001, "603237.SH", 1000, 15.30),
        ],
    )
    r = _run_guard(t)
    expect = round((1000 * 15.10 + 800 * 15.26 + 1000 * 15.30) / 2800, 3)
    _check("A1 同单号多笔成交加权均价=%.3f（非首笔15.1/末笔15.3）" % expect,
           r["avg_price"] == expect, r["avg_price"])


def test_a2_trade_query_exception_returns_none():
    """A2 成交明细接口抛异常 → avg_price=None（不编造价格），且已按重试次数查询。"""
    t = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=None,   # query_stock_trades 直接抛异常
    )
    r = _run_guard(t)
    _check("A2 明细异常 → avg_price=None", r["avg_price"] is None, r["avg_price"])
    _check("A2 成交量仍为 2800（成交是既成事实，不能因取价失败丢失）",
           r["traded_vol"] == 2800, r["traded_vol"])
    _check("A2 已按短轮询重试 %d 次" % G._AVGPRICE_RETRIES,
           t.trade_queries == G._AVGPRICE_RETRIES, t.trade_queries)


def test_a3_incomplete_details_rejected_not_partial_avg():
    """A3 明细量 < 已确认成交量 → 必须返回 None，严禁拿部分明细均价冒充全部真实均价。"""
    t = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=[(1001, "603237.SH", 1000, 15.10)],   # 只同步了 1000/2800
    )
    r = _run_guard(t)
    _check("A3 明细不完整 → avg_price=None（修复前会返回部分均价 15.1）",
           r["avg_price"] is None, r["avg_price"])
    _check("A3 成交量不受影响", r["traded_vol"] == 2800, r["traded_vol"])


def test_a4_details_sync_late_then_complete():
    """A4 明细晚到：前两轮不够、第三轮齐 → 必须返回真实均价（短轮询的价值）。"""
    t = _SeqFillTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fill_seq=[
            (1, [(1001, "603237.SH", 1000, 15.10)]),
            (2, [(1001, "603237.SH", 1800, 15.20)]),
            (3, [(1001, "603237.SH", 2800, 15.25)]),
        ],
    )
    r = _run_guard(t)
    _check("A4 第 3 轮明细齐 → avg_price=15.25", r["avg_price"] == 15.25, r["avg_price"])
    _check("A4 恰好查询 3 次", t.trade_queries == 3, t.trade_queries)


def test_a5_other_order_id_not_counted():
    """A5 混入其它委托号/其它股票的成交明细 → 必须按 order_id 集合严格过滤。"""
    t = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=[
            (1001, "603237.SH", 2800, 15.30),        # 本次委托
            (2002, "603237.SH", 5000, 99.99),        # 其它委托号（昨日挂单残留）
            (1001, "600000.SH", 1000, 88.88),        # 其它股票（脏明细）
        ],
    )
    r = _run_guard(t)
    _check("A5 只计本次 order_id → avg_price=15.3（未被 99.99/88.88 污染）",
           r["avg_price"] == 15.3, r["avg_price"])


def test_a6_resolve_fill_price_three_paths():
    """A6 resolve_fill_price 三条路径：真实均价 / 参考价兜底 / 完全未解析。"""
    px, src = G.resolve_fill_price({"avg_price": 15.312}, 15.24, "603237.SH")
    _check("A6 有真实均价 → (15.312, fill_avg)", (px, src) == (15.312, "fill_avg"), (px, src))

    px, src = G.resolve_fill_price({"avg_price": None}, 15.24, "603237.SH")
    _check("A6 无均价有参考价 → (15.24, ref_price)", (px, src) == (15.24, "ref_price"), (px, src))

    px, src = G.resolve_fill_price({"avg_price": None}, 0.0, "603237.SH")
    _check("A6 两者皆无 → (0.0, unresolved)", (px, src) == (0.0, "unresolved"), (px, src))

    px, src = G.resolve_fill_price({}, 15.24)
    _check("A6 空 dict 也走参考价兜底", (px, src) == (15.24, "ref_price"), (px, src))

    px, src = G.resolve_fill_price({"avg_price": "abc"}, 15.24)
    _check("A6 脏类型均价不炸，回退参考价", (px, src) == (15.24, "ref_price"), (px, src))

    px, src = G.resolve_fill_price({"avg_price": 0}, 0)
    _check("A6 avg_price=0 不算有效（0 价非真实成交价）", (px, src) == (0.0, "unresolved"), (px, src))


def test_a7_record_pending_fill_content(tmpdir):
    """A7 record_pending_fill 落盘内容：字段齐全 + order_ids 全量去重升序（回补要用）。"""
    old = G.PENDING_FILL_FILE
    path = os.path.join(tmpdir, "pending.jsonl")
    try:
        G.PENDING_FILL_FILE = path
        n = G.record_pending_fill(
            "603237.SH", "SELL", 1800, 1002, 15.24,
            note="rebalance_daily:ref_price",
            order_ids=[1002, 1001, 1001, 0, None, "x"],
        )
        _check("A7 落盘返回 1", n == 1, n)
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        _check("A7 写 1 行 JSONL", len(lines) == 1, len(lines))
        rec = json.loads(lines[0])
        _check("A7 code/side/vol 正确",
               (rec["code"], rec["side"], rec["vol"]) == ("603237.SH", "SELL", 1800), rec)
        _check("A7 order_id 保留最后一笔（兼容旧读取）", rec["order_id"] == 1002, rec)
        _check("A7 order_ids 全量去重升序=[1001,1002]（回补不漏前几轮成交）",
               rec["order_ids"] == [1001, 1002], rec.get("order_ids"))
        _check("A7 ref_price/note 留痕",
               rec["ref_price"] == 15.24 and rec["note"] == "rebalance_daily:ref_price", rec)
        _check("A7 account_id 内嵌（防跨账号补价）",
               rec["account_id"] == getattr(C, "ACCOUNT_ID", ""), rec)

        # 落盘失败不得抛异常（记账辅助动作不能打断交易流程）
        G.PENDING_FILL_FILE = os.path.join(tmpdir, "bad", "\0x", "p.jsonl")
        n2 = G.record_pending_fill("603237.SH", "SELL", 100, 1, 1.0)
        _check("A7 落盘失败返回 0 且不抛异常", n2 == 0, n2)
    finally:
        G.PENDING_FILL_FILE = old


def test_a8_production_callers_pending_gate():
    """A8 三个生产调用方：只有非 fill_avg 才落待补队列，且都带 order_ids。"""
    checks = [
        ("rebalance_daily.py", 'if px_src == "fill_avg":'),
        ("qmt_clear.py", 'if px_src != "fill_avg":'),
        ("qmt_monitor.py", 'if r["px_source"] != "fill_avg":'),
    ]
    for fname, gate in checks:
        with open(os.path.join(PROJECT_ROOT, fname), encoding="utf-8") as f:
            src = f.read()
        _check("A8 %s 有留痕门槛 %s" % (fname, gate), gate in src, fname)
        _check("A8 %s 落盘带 order_ids（多轮重挂回补不漏单）" % fname,
               "order_ids=" in src, fname)

    with open(os.path.join(PROJECT_ROOT, "rebalance_daily.py"), encoding="utf-8") as f:
        src = f.read()
    _check("A8 rebalance_daily 三处下单点都走 _mark_unresolved_price",
           src.count("_mark_unresolved_price(") == 4,   # 1 定义 + 3 调用
           src.count("_mark_unresolved_price("))
    _check("A8 rebalance_daily 三处都用 resolve_fill_price",
           src.count("resolve_fill_price(") == 3, src.count("resolve_fill_price("))

    # 真实均价不得被误当异常：_mark_unresolved_price 内必须「先判 fill_avg 直接 return」，
    # 否则每笔正常成交都会进异常列表（告警噪声），也会把待补队列刷爆。
    fn_start = src.index("def _mark_unresolved_price(")
    fn_end = src.index("def main(", fn_start)
    fn_src = src[fn_start:fn_end]
    _check("A8 _mark_unresolved_price 先判 fill_avg 直接 return（不刷异常/不刷队列）",
           'if px_src == "fill_avg":' in fn_src and fn_src.index('if px_src == "fill_avg":')
           < fn_src.index("guard.record_pending_fill("), fn_src[:200])


def test_a9_order_ids_full_list_and_missing_stock_code():
    """A9 多轮重挂必须回传全部 order_ids；缺 stock_code 的脏明细必须剔除。"""
    # A9-1：跨委托号加权 + order_ids 全量回传（回补时按全量单号查明细，不漏前几轮成交）
    t = FakeTrader(
        query_transitions=[(1, (52, 1000, 2800))],   # 等待窗口内先成 1000
        cancel_script={1: (0, (54, 1800, 2800))},    # 撤单受理，处理窗口内又成 800
        auto_fill_from=2,                            # 第 2 笔重挂即全部成交
        fills=[
            (1001, "603237.SH", 1000, 15.10),
            (1001, "603237.SH", 800, 15.26),
            (1002, "603237.SH", 1000, 15.33),
        ],
    )
    r = _run_guard(t)
    _check("A9-1 跨委托号加权均价=15.236",
           r["avg_price"] == round((1000 * 15.10 + 800 * 15.26 + 1000 * 15.33) / 2800, 3),
           r["avg_price"])
    _check("A9-1 order_ids 全量回传=[1001,1002]（回补不漏前几轮成交）",
           r["order_ids"] == [1001, 1002], r.get("order_ids"))

    # A9-2：单号场景 order_ids 也必须存在（不能是 None，否则调用方 record_pending_fill 拿不到）
    t2 = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=[(1001, "603237.SH", 2800, 15.30)],
    )
    r2 = _run_guard(t2)
    _check("A9-2 单笔成交 order_ids=[1001]", r2["order_ids"] == [1001], r2.get("order_ids"))

    # A9-3：零成交时 order_ids 仍回传已有委托号（虽无成交，但委托确实发出过，可查明细）
    t3 = FakeTrader(cancel_script={1: (-1, (50, 0, 2800))}, fills=[])
    r3 = _run_guard(t3)
    _check("A9-3 零成交 avg_price=None", r3["avg_price"] is None, r3["avg_price"])
    _check("A9-3 零成交仍回传 order_ids（不返回 None 免得调用方 .get 拿不到）",
           isinstance(r3["order_ids"], list), r3.get("order_ids"))

    # A9-4：stock_code 缺失 → 必须剔除（不「缺失即信任」），明细不完整 → avg_price=None
    t4 = FakeTrader(
        cancel_script={1: (-1, (54, 2800, 2800))},
        fills=[
            (1001, "", 2800, 15.30),          # 缺 stock_code → 不可信，剔除
            (1001, "600000.SH", 2800, 88.88),  # 其它标的 → 剔除
        ],
    )
    r4 = _run_guard(t4)
    _check("A9-4 缺 stock_code / 异标的明细全部剔除 → avg_price=None（不写入可疑均价）",
           r4["avg_price"] is None, r4["avg_price"])
    _check("A9-4 成交量不受剔除影响", r4["traded_vol"] == 2800, r4["traded_vol"])

    # A9-5：_norm_code 容错——'SH603237' / '603237' / '603237.sh' 必须归一到同一标的
    _check("A9-5 _norm_code('603237.SH')", G._norm_code("603237.SH") == "603237.SH", G._norm_code("603237.SH"))
    _check("A9-5 _norm_code('SH603237') 归一", G._norm_code("SH603237") == "603237.SH", G._norm_code("SH603237"))
    _check("A9-5 _norm_code('603237.sh') 大小写归一", G._norm_code("603237.sh") == "603237.SH", G._norm_code("603237.sh"))
    _check("A9-5 _norm_code('  603237.SZ  ') 去空格", G._norm_code("  603237.SZ  ") == "603237.SZ", G._norm_code("  603237.SZ  "))
    _check("A9-5 _norm_code(None) → ''（不抛异常）", G._norm_code(None) == "", G._norm_code(None))


if __name__ == "__main__":
    print("== order_guard 撤单窗口成交补记 + 双重委托防护 回归测试（T-20260930-001）==")
    print("== 含 真实成交均价 avg_price + 待补价队列 专属用例（T-20261002-001）==\n")
    import shutil
    import tempfile
    _real_sleep = time.sleep
    _real_defaults = G.wait_order_fill.__defaults__
    _real_poll = G.POLL_INTERVAL
    _real_retries = G._AVGPRICE_RETRIES
    _tmpdir = tempfile.mkdtemp(prefix="pfill_")
    try:
        # 提速：单轮等待 1s、轮询间隔/守护 sleep 压到 20ms（不改变逻辑分支）
        G.wait_order_fill.__defaults__ = (1,)
        G.POLL_INTERVAL = 0.05
        time.sleep = lambda s: _real_sleep(min(s, 0.02))
        print("-- T-20260930-001 撤单补记 / 防双倍 --")
        test_s1_incident_cancel_fail_window_filled()
        test_s2_cancel_ok_window_backfill_exact_replacement()
        test_s3_exhausted_partial_traded_vol_kept()
        test_s4_active_order_never_replaced()
        test_s5_booking_gate_source_assertions()
        print("\n-- T-20261002-001 真实成交均价 / 待补价队列 --")
        test_a1_single_order_multi_trades_weighted()
        test_a2_trade_query_exception_returns_none()
        test_a3_incomplete_details_rejected_not_partial_avg()
        test_a4_details_sync_late_then_complete()
        test_a5_other_order_id_not_counted()
        test_a6_resolve_fill_price_three_paths()
        test_a7_record_pending_fill_content(_tmpdir)
        test_a8_production_callers_pending_gate()
        test_a9_order_ids_full_list_and_missing_stock_code()
    finally:
        time.sleep = _real_sleep
        G.wait_order_fill.__defaults__ = _real_defaults
        G.POLL_INTERVAL = _real_poll
        G._AVGPRICE_RETRIES = _real_retries
        shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
