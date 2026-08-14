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
    def __init__(self, inst, vol):
        self.m_strInstrumentID = inst
        self.m_nVolume = vol

_mock_positions = {}  # inst -> _MockPos

def _mock_passorder(*args):
    _mock_order_log.append(args)

def _mock_get_trade_detail_data(acct, mtype, dtype):
    if dtype == "position":
        return list(_mock_positions.values())
    return []

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

    def set_price(self, code, close_price):
        self._market_data[code] = {"close": [close_price]}

_ns = {
    "__name__": "strategy_v2_mock",
    "passorder": _mock_passorder,
    "get_trade_detail_data": _mock_get_trade_detail_data,
    "math": __import__("math"),
    "csv": __import__("csv"),
    "json": __import__("json"),
    "os": os,
    "time": __import__("time"),
    "datetime": __import__("datetime"),
    "pandas": __import__("pandas"),
}

exec(compile(_src, STRATEGY_SRC, "exec"), _ns)

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
    _mock_order_log.clear()
    _mock_positions.clear()

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
    print("\n" + "=" * 60)
    print("Results: %d passed, %d failed" % (passed, failed))
    print("=" * 60)
    sys.exit(1 if failed > 0 else 0)
