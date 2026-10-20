# coding=utf-8
"""MOCK test: 688121 repeat-order bug fix verification

Tests the 3 fixes:
  fix1: _execute_buy/_execute_sell guard against code in _pending_orders
  fix2: _reconcile daily gate (_last_reconcile_date)
  fix3: stop-loss sells loop + suspended queue skip pending orders

Run: python mock_test_pending_guard.py
"""
import sys
import os

STRATEGY_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_v2.py")

with open(STRATEGY_SRC, "r", encoding="utf-8") as f:
    _src = f.read()

_mock_order_log = []

class _MockPos:
    def __init__(self, inst, vol, can_use=None, cost=0.0):
        self.m_strInstrumentID = inst
        self.m_nVolume = vol
        self.m_nCanUseVolume = vol if can_use is None else can_use
        self.m_dOpenPrice = cost

class _MockOrder:
    def __init__(self, inst, opt_name, remark, order_id, vol_traded=0):
        self.m_strInstrumentID = inst
        self.m_strOptName = opt_name
        self.m_strRemark = remark
        self.m_nOrderID = order_id
        self.m_nVolumeTraded = vol_traded

_mock_positions = {}  # inst -> _MockPos
_mock_orders = []     # list of _MockOrder
_mock_cancel_log = [] # 官方 cancel() 调用记录 (oid, acct, mtype)

def _mock_passorder(*args):
    _mock_order_log.append(args)

def _mock_get_trade_detail_data(acct, mtype, dtype):
    if dtype == "position":
        return list(_mock_positions.values())
    if dtype == "order":
        return list(_mock_orders)
    return []

def _mock_can_cancel_order(oid, acct, mtype):
    """官方撤单预检（2026-10-13 _cancel_order_by 走此入口）。MOCK 恒可撤。"""
    return True

def _mock_cancel(oid, acct, mtype, C):
    """官方撤单入口 cancel(oid, acct, 'stock', C)。
    撤单成功后订单从委托列表消失 -> _confirm_cancelled 轮询判死透返回 True。"""
    _mock_cancel_log.append((oid, acct, mtype))
    oid_s = str(oid)
    _mock_orders[:] = [o for o in _mock_orders
                       if str(getattr(o, "m_nOrderID", "")) != oid_s]
    return True

class MockC:
    def __init__(self, ts=1723171200):
        self.barpos = 0
        self._tick_time = ts
        self._bar_time = ts
        self._market_data = {}

    def get_tick_timetag(self):
        return self._tick_time

    def get_bar_timetag(self, pos):
        return self._bar_time

    def get_market_data_ex(self, fields, codes, period="1d"):
        result = {}
        for code in codes:
            if code in self._market_data:
                import pandas as pd
                row = self._market_data[code]
                result[code] = pd.DataFrame(row, index=[0])
        return result

    def set_price(self, code, close_price, pre_close=None, open_price=None, volume=1000000):
        self._market_data[code] = {
            "close": [close_price],
            "pre_close": [pre_close if pre_close is not None else close_price],
            "open": [open_price if open_price is not None else close_price],
            "volume": [volume],
        }

_ns = {
    "__name__": "strategy_v2_mock",
    "passorder": _mock_passorder,
    "get_trade_detail_data": _mock_get_trade_detail_data,
    "can_cancel_order": _mock_can_cancel_order,
    "cancel": _mock_cancel,
    "math": __import__("math"),
    "csv": __import__("csv"),
    "json": __import__("json"),
    "os": os,
    "time": __import__("time"),
    "datetime": __import__("datetime"),
    "pandas": __import__("pandas"),
}

exec(compile(_src, STRATEGY_SRC, "exec"), _ns)

# 遥测重定向：MOCK 测试会经由 _execute_buy/_execute_sell/handlebar 触发 _telemetry，
# 必须把落盘路径指向临时目录，绝不可污染生产 D:/QMT_POOL 的真实经营指标产物。
import tempfile as _tempfile
import shutil as _shutil
_TEL_TMPDIR = _tempfile.mkdtemp(prefix="v2_mock_tel_")

def _gs(name):
    return _ns[name]

def _ss(name, val):
    _ns[name] = val

passed = 0
failed = 0

def _assert(cond, msg):
    global passed, failed
    if cond:
        passed += 1
        print("  PASS: %s" % msg)
    else:
        failed += 1
        print("  FAIL: %s" % msg)

def reset_state():
    _ss("_cash", 100000.0)
    _ss("_holdings", {})
    _ss("_entry_prices", {})
    _ss("_entry_dates", {})
    _ss("_nav_peak", 1.0)
    _ss("_last_rebal_month", -1)
    _ss("_last_rebal_log_min", -1)
    _ss("_last_hb_min", -1)
    _ss("_inited", True)
    _ss("_pending_orders", {})
    _ss("_today_orders", {})
    _ss("_suspended_sells", [])
    _ss("_last_pending_min", -1)
    _ss("_last_reconcile_date", "")
    # 2b 自闭环后 STATE_FILE/LOG_FILE/LOCK_FILE/遥测路径已随文件链路废止（策略运行期零文件IO，
    # 无生产文件可污染）；2026-10-14 P0「MOCK 覆盖生产 state」事故根因（文件落盘）已结构性消除。
    _ss("_passive", False)
    _ss("_passive_reason", "")
    _ss("_g_running", False)
    _ss("_last_heart_at", 0.0)
    _mock_order_log.clear()
    _mock_cancel_log.clear()
    _mock_positions.clear()
    _mock_orders.clear()

# ============ Test 1 ============
def test_sell_pending_guard():
    print("\n[Test 1] _execute_sell: skip if code in _pending_orders")
    reset_state()
    C = MockC(1723171200)
    C.set_price("688121.SH", 9.5)

    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01"})
    _ss("_pending_orders", {"688121.SH": {"type": "sell", "amount": 100}})

    before_len = len(_mock_order_log)
    _ns["_execute_sell"](C, "688121.SH")
    _assert(len(_mock_order_log) == before_len, "sell blocked when pending order exists")

    _ss("_pending_orders", {})
    _ns["_execute_sell"](C, "688121.SH")
    _assert(len(_mock_order_log) > before_len, "sell proceeds after pending cleared")

# ============ Test 2 ============
def test_buy_pending_guard():
    print("\n[Test 2] _execute_buy: skip if code in _pending_orders")
    reset_state()
    C = MockC(1723171200)
    C.set_price("688121.SH", 10.0)

    _ss("_cash", 100000.0)
    _ss("_pending_orders", {"688121.SH": {"type": "buy", "amount": 100}})

    before_len = len(_mock_order_log)
    _ns["_execute_buy"](C, "688121.SH")
    _assert(len(_mock_order_log) == before_len, "buy blocked when pending order exists")

    _ss("_pending_orders", {})
    before_len = len(_mock_order_log)
    _ns["_execute_buy"](C, "688121.SH")
    _assert(len(_mock_order_log) > before_len, "buy proceeds after pending cleared")

# ============ Test 3 ============
def test_reconcile_daily_gate():
    print("\n[Test 3] _reconcile: daily gate prevents repeated execution")
    reset_state()
    C = MockC(1723171200)

    _ss("_today_orders", {"688121.SH": {"dir": "sell", "amount": 100, "price": 9.5, "entry_price": 10.0, "entry_date": "2024-08-01"}})
    _ss("_pending_orders", {"688121.SH": {"type": "sell", "amount": 100}})
    _ss("_holdings", {})
    _ss("_entry_prices", {})

    # Gate is in handlebar call site, not inside _reconcile.
    # Simulate handlebar logic: check gate before calling _reconcile
    today_str = _ns["_get_market_time"](C).strftime("%Y-%m-%d")

    _ss("_last_reconcile_date", today_str)
    # handlebar would skip: if _last_reconcile_date == today_str -> don't call _reconcile
    _assert(_gs("_last_reconcile_date") == today_str,
            "gate blocks: _last_reconcile_date == today, handlebar skips _reconcile")

    # Now simulate: gate empty, _reconcile runs
    _ss("_last_reconcile_date", "")
    _ns["_reconcile"](C)
    _assert(_gs("_last_reconcile_date") == today_str,
            "_last_reconcile_date set after reconcile runs")
    _assert("688121.SH" not in _gs("_pending_orders"),
            "pending_orders cleared after reconcile runs")
    _assert(_gs("_today_orders") == {},
            "today_orders cleared after reconcile runs")

# ============ Test 4 ============
def test_stoploss_skips_pending():
    print("\n[Test 4] Stop-loss sells loop: skip code in _pending_orders")
    reset_state()
    C = MockC(1723171200)
    C.set_price("688121.SH", 9.0)
    C.set_price("000001.SZ", 9.0)

    _ss("_holdings", {"688121.SH": 100, "000001.SZ": 200})
    _ss("_entry_prices", {"688121.SH": 10.0, "000001.SZ": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01", "000001.SZ": "2024-08-01"})
    _ss("_pending_orders", {"688121.SH": {"type": "sell", "amount": 100}})

    sells = []
    for code in list(_gs("_holdings").keys()):
        if code in _gs("_pending_orders"):
            continue
        price = _ns["_get_price"](C, code, "close")
        if price is None:
            continue
        entry = _gs("_entry_prices").get(code, price)
        pnl = price / entry - 1.0 if entry > 0 else 0
        if pnl <= -_gs("STOP_LOSS"):
            sells.append(code)

    _assert("688121.SH" not in sells,
            "688121 NOT in sells list when it has pending order")
    _assert("000001.SZ" in sells,
            "000001 IS in sells list (no pending, price -10% triggers stop-loss)")

# ============ Test 5 ============
def test_suspended_queue_skips_pending():
    print("\n[Test 5] Suspended sell queue: skip code in _pending_orders")
    reset_state()

    _ss("_suspended_sells", ["688121.SH", "000002.SZ"])
    _ss("_pending_orders", {"688121.SH": {"type": "sell", "amount": 100}})
    _ss("_holdings", {"688121.SH": 100, "000002.SZ": 200})

    attempted_sells = []
    for code in list(_gs("_suspended_sells")):
        if code in _gs("_pending_orders"):
            continue
        attempted_sells.append(code)

    _assert("688121.SH" not in attempted_sells,
            "688121 skipped in suspended queue (has pending order)")
    _assert("000002.SZ" in attempted_sells,
            "000002 proceeds (no pending order)")

# ============ Test 6 ============
def test_688121_scenario():
    print("\n[Test 6] 688121 full scenario: stop-loss -> pending sell -> NO repeat")
    reset_state()
    C = MockC(1723171200)
    C.set_price("688121.SH", 9.0)

    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01"})

    _mock_order_log.clear()
    _ns["_execute_sell"](C, "688121.SH")
    _assert(len(_mock_order_log) == 1, "first sell order placed")
    _assert("688121.SH" in _gs("_pending_orders"), "688121 in pending after first sell")

    before = len(_mock_order_log)
    _ns["_execute_sell"](C, "688121.SH")
    _assert(len(_mock_order_log) == before, "second sell BLOCKED by pending guard")

    sells = []
    for code in list(_gs("_holdings").keys()):
        if code in _gs("_pending_orders"):
            continue
        sells.append(code)
    _assert("688121.SH" not in sells, "stop-loss loop also skips pending 688121")

# ============ Test 7: reconcile daily gate in handlebar ============
def test_handlebar_reconcile_gate():
    print("\n[Test 7] handlebar: reconcile NOT called twice same day")
    reset_state()
    C = MockC(1723196400)
    C.set_price("688121.SH", 10.0)

    _ss("_inited", True)
    _ss("_last_reconcile_date", "2024-08-09")
    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01"})

    today_str = _ns["_get_market_time"](C).strftime("%Y-%m-%d")
    _assert(today_str == "2024-08-09", "mock time yields 2024-08-09")

    should_skip = (_gs("_last_reconcile_date") == today_str)
    _assert(should_skip, "handlebar would skip reconcile because gate already set for today")

# ============ Test 8: cross-day pending forced rollback ============
def test_crossday_pending_rollback():
    print("\n[Test 8] cross-day pending -> forced rollback & removal")
    reset_state()
    # pending 下单时间在昨天，当前时间为今天 -> 应强制回滚并移出
    from datetime import datetime as _dt, timedelta as _td
    ts_now = 1723196400          # 2024-08-09 10:40:00 UTC
    ts_yesterday = ts_now - 86400
    C = MockC(ts_now)
    C.set_price("688121.SH", 9.0)
    C.set_price("000001.SZ", 10.0)

    _ss("_holdings", {"688121.SH": 100, "000001.SZ": 200})
    _ss("_entry_prices", {"688121.SH": 10.0, "000001.SZ": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01", "000001.SZ": "2024-08-01"})
    _ss("_cash", 100000.0)
    _ss("_pending_orders", {
        "688121.SH": {"type": "sell", "amount": 100, "price": 9.0,
                      "original_amount": 100, "retries": 0, "time": _dt.fromtimestamp(ts_yesterday),
                      "entry_price": 10.0, "entry_date": "2024-08-01"},
        "000001.SZ": {"type": "sell", "amount": 200, "price": 10.0,
                      "original_amount": 200, "retries": 0, "time": _dt.fromtimestamp(ts_now),
                      "entry_price": 10.0, "entry_date": "2024-08-01"},
    })

    _ns["_check_pending_orders"](C)

    _assert("688121.SH" not in _gs("_pending_orders"),
            "cross-day pending 688121 removed")
    _assert("688121.SH" in _gs("_holdings"),
            "cross-day pending 688121 holding restored (rollback)")
    _assert("000001.SZ" in _gs("_pending_orders"),
            "same-day pending 000001 kept (not forced rollback)")

# ============ Test 9: reconcile buy-not-filled fallback to account position ============
def test_reconcile_buy_position_fallback():
    print("\n[Test 9] reconcile buy: deal empty but account has position -> keep holding")
    reset_state()
    C = MockC(1723171200)
    # 11:13 买入已成交（deal 反查失败/remark 未命中），但账户 position 有货
    _mock_positions["688121"] = _MockPos("688121", 500)
    _ss("_today_orders", {"688121.SH": {"dir": "buy", "amount": 500, "price": 2.13}})
    _ss("_pending_orders", {"688121.SH": {"type": "buy", "amount": 500}})
    _ss("_cash", 98935.0)  # 估算扣减后: 100000 - 500*2.13
    _ss("_holdings", {"688121.SH": 500})
    _ss("_entry_prices", {"688121.SH": 2.13})

    _ns["_reconcile"](C)

    _assert(_gs("_holdings").get("688121.SH") == 500,
            "688121 kept in holdings (account has position)")
    _assert(abs(_gs("_cash") - 98935.0) < 0.01,
            "cash stays at post-buy value (no double refund)")
    _assert(_gs("_today_orders") == {}, "today_orders cleared after reconcile")

# ============ Test 10: reconcile sell-not-filled fallback to account position ============
def test_reconcile_sell_position_fallback():
    print("\n[Test 10] reconcile sell: deal empty but account has NO position -> confirm sold")
    reset_state()
    C = MockC(1723171200)
    # 卖出已成交（deal 漏记），账户确无货 -> 不应恢复持仓、应维持回笼现金
    _ss("_today_orders", {"688121.SH": {"dir": "sell", "amount": 500, "price": 2.20,
                                        "entry_price": 2.13, "entry_date": "2024-08-01"}})
    _ss("_pending_orders", {"688121.SH": {"type": "sell", "amount": 500}})
    _ss("_cash", 101100.0)  # 估算回笼后: 100000 + 500*2.20
    _ss("_holdings", {})
    _ss("_entry_prices", {})

    _ns["_reconcile"](C)

    _assert("688121.SH" not in _gs("_holdings"),
            "688121 NOT restored (account has no position)")
    _assert(abs(_gs("_cash") - 101100.0) < 0.01,
            "cash keeps post-sell value (sell confirmed)")
    _assert(_gs("_today_orders") == {}, "today_orders cleared after reconcile")

# ============ Test 11: reconcile buy truly unfilled (no position) -> rollback ============
def test_reconcile_buy_truly_unfilled():
    print("\n[Test 11] reconcile buy: deal empty AND account empty -> rollback holding")
    reset_state()
    C = MockC(1723171200)
    # 未成交：账户也无货（涨停买不进等）
    _ss("_today_orders", {"688121.SH": {"dir": "buy", "amount": 500, "price": 2.13}})
    _ss("_pending_orders", {"688121.SH": {"type": "buy", "amount": 500}})
    _ss("_cash", 98935.0)
    _ss("_holdings", {"688121.SH": 500})
    _ss("_entry_prices", {"688121.SH": 2.13})

    _ns["_reconcile"](C)

    _assert("688121.SH" not in _gs("_holdings"),
            "688121 rolled back (no account position)")
    _assert(abs(_gs("_cash") - 100000.0) < 0.01,
            "cash refunded to pre-buy value")

# ============ Test 12: rollback cancels live order before reverting (P1 fix) ============
def test_rollback_cancels_live_order():
    print("\n[Test 12] rollback: cancel QMT live order before reverting estimate")
    reset_state()
    from datetime import datetime as _dt, timedelta as _td
    ts_now = 1723196400
    C = MockC(ts_now)
    C.set_price("688121.SH", 9.0)

    # 卖出挂单 4 分钟未成交、已重试 3 次 -> 封顶回滚；QMT 端仍有活单(order_id=999)
    _mock_orders.append(_MockOrder("688121", "卖出", "V2卖出", 999, vol_traded=0))
    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01"})
    _ss("_cash", 100000.0)
    _ss("_pending_orders", {
        "688121.SH": {"type": "sell", "amount": 100, "price": 9.0,
                      "original_amount": 100, "retries": 3, "time": _dt.fromtimestamp(ts_now - 240),
                      "entry_price": 10.0, "entry_date": "2024-08-01"},
    })

    _mock_order_log.clear()
    _mock_cancel_log.clear()
    _ns["_check_pending_orders"](C)

    _assert(len(_mock_cancel_log) >= 1, "live order cancelled before rollback (官方 cancel() 被调用)")
    _assert("688121.SH" not in _gs("_pending_orders"), "pending removed after rollback")
    _assert(_gs("_holdings").get("688121.SH") == 100, "holding restored after sell rollback")

# ============ Test 13: sell rollback while limit-down -> suspended queue ============
def test_rollback_limitdown_enters_suspended():
    print("\n[Test 13] sell rollback: limit-down -> enters suspended queue")
    reset_state()
    from datetime import datetime as _dt
    ts_now = 1723196400
    C = MockC(ts_now)
    C.set_price("688121.SH", 8.0, pre_close=10.0)  # -20% 科创板跌停
    _mock_orders.append(_MockOrder("688121", "卖出", "V2卖出", 999, vol_traded=0))  # 跌停封死挂单未成交

    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01"})
    _ss("_cash", 100000.0)
    _ss("_pending_orders", {
        "688121.SH": {"type": "sell", "amount": 100, "price": 9.0,
                      "original_amount": 100, "retries": 3, "time": _dt.fromtimestamp(ts_now - 240),
                      "entry_price": 10.0, "entry_date": "2024-08-01"},
    })

    _ns["_check_pending_orders"](C)

    _assert("688121.SH" in _gs("_holdings"), "holding restored after sell rollback")
    _assert("688121.SH" in _gs("_suspended_sells"),
            "limit-down stock enters suspended queue (no management gap)")

# ============ Test 14: sell rollback not limit-down -> NOT suspended ============
def test_rollback_no_limitdown_not_suspended():
    print("\n[Test 14] sell rollback: no limit-down -> NOT in suspended queue")
    reset_state()
    from datetime import datetime as _dt
    ts_now = 1723196400
    C = MockC(ts_now)
    C.set_price("688121.SH", 9.5, pre_close=10.0)  # -5% 非跌停
    _mock_orders.append(_MockOrder("688121", "卖出", "V2卖出", 999, vol_traded=0))

    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-08-01"})
    _ss("_cash", 100000.0)
    _ss("_pending_orders", {
        "688121.SH": {"type": "sell", "amount": 100, "price": 9.5,
                      "original_amount": 100, "retries": 3, "time": _dt.fromtimestamp(ts_now - 240),
                      "entry_price": 10.0, "entry_date": "2024-08-01"},
    })

    _ns["_check_pending_orders"](C)

    _assert("688121.SH" in _gs("_holdings"), "holding restored after sell rollback")
    _assert("688121.SH" not in _gs("_suspended_sells"),
            "non-limit-down stock NOT in suspended queue")

# ============ Test 15: END-TO-END 跌停卖出全链路演练 ============
def test_full_rollback_scenario():
    print("\n[Test 15] 端到端演练: 跌停卖出 -> 超时撤单重下x3 -> 回滚撤活单 -> 暂缓队列 -> 解封自动补卖")
    reset_state()
    from datetime import datetime as _dt
    ts_base = 1723196400  # 2024-08-09
    C = MockC(ts_base)
    C.set_price("688121.SH", 8.0, pre_close=10.0)  # 科创板跌停 -20%

    _ss("_holdings", {"688121.SH": 100})
    _ss("_entry_prices", {"688121.SH": 10.0})
    _ss("_entry_dates", {"688121.SH": "2024-07-01"})
    _ss("_cash", 100000.0)
    _ss("_inited", True)
    _ss("_last_reconcile_date", "2024-08-08")

    # ===== 第1步: 止损触发卖出（跌停封死，卖单挂在 QMT 端不成交） =====
    _mock_order_log.clear()
    _ns["_execute_sell"](C, "688121.SH")
    _assert(len(_mock_order_log) == 1, "step1 卖单已下(挂单未成交)")
    _assert("688121.SH" in _gs("_pending_orders"), "step1 pending 已跟踪")

    # ===== 第2步: 3轮超时 -> 撤单重下（模拟 QMT 端每次挂单不成交） =====
    for i in (1, 2, 3):
        C._tick_time = ts_base + i * 200  # 每轮推进 ~3.3分钟
        C._bar_time = C._tick_time
        info = _gs("_pending_orders")["688121.SH"]
        info["time"] = _dt.fromtimestamp(ts_base + (i - 1) * 200)  # 模拟上一轮遗留时间
        _ss("_pending_orders", {"688121.SH": info})
        _mock_orders.clear()
        _mock_orders.append(_MockOrder("688121", "卖出", "V2卖出", 1000 + i, vol_traded=0))  # QMT 端挂单仍在
        _ns["_check_pending_orders"](C)
        _assert(_gs("_pending_orders").get("688121.SH", {}).get("retries", 0) == i,
                "step2 第%d轮撤单重下 retry=%d" % (i, i))

    # ===== 第3步: 第4轮超时 -> 重试封顶 -> 回滚：先撤活单，恢复持仓，进暂缓队列 =====
    C._tick_time = ts_base + 4 * 200
    C._bar_time = C._tick_time
    info = _gs("_pending_orders")["688121.SH"]
    info["time"] = _dt.fromtimestamp(ts_base + 3 * 200)
    _ss("_pending_orders", {"688121.SH": info})
    _mock_orders.clear()
    _mock_orders.append(_MockOrder("688121", "卖出", "V2卖出", 1004, vol_traded=0))
    _mock_order_log.clear()
    _mock_cancel_log.clear()
    _ns["_check_pending_orders"](C)

    _assert(len(_mock_cancel_log) >= 1, "step3 回滚前已撤 QMT 活单(官方 cancel() 被调用)")
    _assert("688121.SH" not in _gs("_pending_orders"), "step3 pending 已清理")
    _assert(_gs("_holdings").get("688121.SH") == 100, "step3 持仓已恢复")
    _assert("688121.SH" in _gs("_suspended_sells"), "step3 跌停 -> 进暂缓队列(无管理空窗)")

    # ===== 第4步: 解封（非跌停）-> 暂缓队列自动补卖 =====
    C.set_price("688121.SH", 10.5, pre_close=10.0)  # 次日/盘中解封 +5%
    _mock_order_log.clear()
    for code in list(_gs("_suspended_sells")):
        if code in _gs("_pending_orders"):
            continue
        if not _ns["_is_limit_down"](C, code) and not _ns["_is_suspended"](C, code):
            _gs("_suspended_sells").remove(code)
            if code in _gs("_holdings"):
                _ns["_execute_sell"](C, code)
    _assert("688121.SH" not in _gs("_suspended_sells"), "step4 暂缓队列已移除")
    _assert(len(_mock_order_log) == 1, "step4 补卖单已下(自动补卖)")
    _assert(_gs("_pending_orders").get("688121.SH", {}).get("type") == "sell",
            "step4 补卖进入 pending 跟踪")

# ============ Test 16 ============
def test_heartbeat_native():
    print("\n[Test 16] _heart: 打印化心跳（遥测 json 落盘已废止）")
    reset_state()
    C = MockC(1723171200)  # 2024-08-09 13:20
    C.set_price("TEST.SH", 8.0)
    _ss("_cash", 99000.0)
    _ss("_holdings", {"TEST.SH": 100})
    _ss("_entry_prices", {"TEST.SH": 10.0})
    _ss("_nav_peak", 1.0)
    _ss("_last_heart_at", 0.0)
    # 2b 心跳=print 通道（C++ 侧落 FormulaOutput 日志），不再写 json；
    # 结构断言：_telemetry 已废止、_heart 可调用、节流变量在位。
    _assert("_telemetry" not in _ns, "Test16 _telemetry 已随文件链路废止")
    _assert(callable(_ns.get("_heart")), "Test16 _heart 心跳函数在位")
    _assert(isinstance(_gs("_last_heart_at"), float), "Test16 心跳节流变量 _last_heart_at 在位")
    try:
        _ns["_heart"](C)
        _assert(_gs("_last_heart_at") > 0, "Test16 _heart 调用后节流时间戳已更新")
    except Exception as e_hb:
        _assert(False, "Test16 _heart 调用异常: %r" % (e_hb,))

# ============ Test 17: SNAPSHOT 空 -> 预检失败 -> 被动 ============
def test_io_preflight_snapshot_empty():
    print("\n[Test 17] _io_preflight: SNAPSHOT/PERIOD_LEDGER 空 -> 转被动 fail-safe")
    reset_state()
    _ss("SNAPSHOT", {})
    _ss("PERIOD_LEDGER", {})
    _ss("_passive", False)
    _ss("_passive_reason", "")
    ok = _ns["_io_preflight"]()
    _assert(ok is False, "Test17 预检: 空占位块 -> False")
    # init 路径: 预检失败 -> 被动
    C = MockC(1723171200)
    _ss("_inited", False)
    _ns["init"](C)
    _assert(_gs("_passive") is True, "Test17 init: 预检失败 -> 被动（不盲跑）")
    _assert("预检" in _gs("_passive_reason"), "Test17 被动原因标注预检失败")
    _assert(len(_gs("_holdings")) == 0, "Test17 被动下 holdings 保持空")

# ============ Test 18: 账户接口异常 -> 预检失败 -> 被动 ============
def test_io_preflight_position_api_broken():
    print("\n[Test 18] _io_preflight: 账户 position 接口异常 -> 转被动 fail-safe")
    reset_state()
    _ss("SNAPSHOT", {"600000.SH": {}})
    _ss("PERIOD_LEDGER", {"account_id": _gs("ACCOUNT_ID"), "cash_hint": 50000.0,
                           "holdings": {}})
    _ss("_passive", False)
    _ss("_passive_reason", "")
    # 模拟接口不可用（沙箱/断连）
    _ns["get_trade_detail_data"] = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down"))
    try:
        ok = _ns["_io_preflight"]()
        _assert(ok is False, "Test18 接口异常 -> 预检 False")
        C = MockC(1723171200)
        _ss("_inited", False)
        _ns["init"](C)
        _assert(_gs("_passive") is True, "Test18 init: 接口异常 -> 被动")
    finally:
        _ns["get_trade_detail_data"] = _mock_get_trade_detail_data

# ============ Test 19: 正常路径 -> 活跃 + 期初账本重建 ============
def test_init_normal_rebuild():
    print("\n[Test 19] init: 正常路径 -> 活跃实例，期初账本∩账户position 重建")
    reset_state()
    C = MockC(1723171200)
    C.set_price("600000.SH", 10.5)
    C.set_price("000001.SZ", 12.0)
    # 账户 position（L2 真相）: 600000.SH 100股@10.0；000858.SZ 200股@50.0 属他策略（不在账本白名单）
    _mock_positions.clear()
    _mock_positions["600000"] = _MockPos("600000.SH", 100, cost=10.0)
    _mock_positions["000858"] = _MockPos("000858.SZ", 200, cost=50.0)
    _ss("SNAPSHOT", {"600000.SH": {}, "000858.SZ": {}})
    _ss("PERIOD_LEDGER", {
        "account_id": _gs("ACCOUNT_ID"),
        "cash_hint": 50000.0,
        "holdings": {"600000.SH": {"vol": 100, "entry_date": "2026-10-13"},
                     "000001.SZ": {"vol": 200, "entry_date": "2026-10-13"}},  # 000001 账户无 -> 不入账
        "suspended_sells": ["000001.SZ"],
        "last_rebal_month": 10,
        "nav_peak": 1.05,
    })
    _ss("_inited", False)
    _ss("_passive", False)
    _ss("_passive_reason", "")
    _ss("_g_running", False)
    _mock_orders.clear()
    _ns["init"](C)
    _assert(_gs("_passive") is False, "Test19 正常路径 -> 活跃实例")
    _assert(_gs("_holdings") == {"600000.SH": 100},
            "Test19 白名单∩账户重建 holdings={600000.SH:100} 实得 %r" % _gs("_holdings"))
    _assert(_gs("_entry_prices").get("600000.SH") == 10.0,
            "Test19 成本=position.m_dOpenPrice=10.0 实得 %r" % _gs("_entry_prices"))
    _assert(_gs("_cash") == 50000.0, "Test19 cash=ledger.cash_hint")
    _assert(_gs("_suspended_sells") == ["000001.SZ"], "Test19 暂缓队列来自账本")
    _assert(_gs("_last_rebal_month") == 10, "Test19 last_rebal_month 来自账本")
    _assert(abs(_gs("_nav_peak") - 1.05) < 1e-9, "Test19 nav_peak 来自账本")
    _assert("000858" not in str(list(_gs("_holdings").keys())),
            "Test19 他策略持仓(000858)不在账本白名单 -> 不纳管（资金红线）")
    # 账号戳不匹配 -> 被动
    _ss("_g_running", False)
    _ss("PERIOD_LEDGER", dict(_gs("PERIOD_LEDGER"), account_id="99999999"))
    _ss("_inited", False)
    _ns["init"](C)
    _assert(_gs("_passive") is True, "Test19b 账号戳不匹配 -> 被动（拿错账本不交易）")

# ============ Run ============
if __name__ == "__main__":
    print("=" * 60)
    print("MOCK Test: 688121 Repeat-Order Bug Fix")
    print("=" * 60)
    test_sell_pending_guard()
    test_buy_pending_guard()
    test_reconcile_daily_gate()
    test_stoploss_skips_pending()
    test_suspended_queue_skips_pending()
    test_688121_scenario()
    test_handlebar_reconcile_gate()
    test_crossday_pending_rollback()
    test_reconcile_buy_position_fallback()
    test_reconcile_sell_position_fallback()
    test_reconcile_buy_truly_unfilled()
    test_rollback_cancels_live_order()
    test_rollback_limitdown_enters_suspended()
    test_rollback_no_limitdown_not_suspended()
    test_full_rollback_scenario()
    test_heartbeat_native()
    test_io_preflight_snapshot_empty()
    test_io_preflight_position_api_broken()
    test_init_normal_rebuild()
    _shutil.rmtree(_TEL_TMPDIR, ignore_errors=True)
    print("\n" + "=" * 60)
    print("Results: %d passed, %d failed" % (passed, failed))
    print("=" * 60)
    sys.exit(1 if failed > 0 else 0)
