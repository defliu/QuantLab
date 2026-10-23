# -*- coding: utf-8 -*-
"""模拟盘撮合器单测 —— 费用/涨跌停/整手/出场规则。

跑法：
  python -m pytest projects/Project_23_每日模拟盘/tests/test_execution.py -v
或直接：
  python projects/Project_23_每日模拟盘/tests/test_execution.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

from paper import execution as EX  # noqa: E402


def test_fee_min_commission():
    """小额成交按最低佣金 5 元收取。"""
    # 1000 元买入：佣金 max(5, 1000*0.0002=0.2) = 5
    fee = EX.buy_fee(1000.0, slip=0.0)
    assert abs(fee - (5.0 + 1000.0 * 0.00001)) < 1e-6, fee


def test_fee_sell_includes_stamp():
    """卖出含印花税万5，买入不含。"""
    amt = 100000.0
    b = EX.buy_fee(amt, slip=0.0)
    s = EX.sell_fee(amt, slip=0.0)
    assert abs((s - b) - amt * 0.0005) < 1e-6, (b, s)


def test_fee_slippage():
    """滑点按成交额线性计入。"""
    amt = 100000.0
    assert abs((EX.buy_fee(amt, 0.001) - EX.buy_fee(amt, 0.0)) - amt * 0.001) < 1e-6


def test_lot_floor():
    assert EX.lot_floor(150) == 100
    assert EX.lot_floor(99) == 0
    assert EX.lot_floor(1999) == 1900
    assert EX.lot_floor(None) == 0


def test_price_limit_by_board():
    assert EX.price_limit_pct("600000.SH") == 0.10
    assert EX.price_limit_pct("300001.SZ") == 0.20
    assert EX.price_limit_pct("688001.SH") == 0.20
    assert EX.price_limit_pct("830001.BJ") == 0.30
    assert EX.price_limit_pct("600000.SH", is_st=True) == 0.05


def test_can_buy_limit_up_open():
    """一字涨停（开盘价=前收×1.1）买不进。"""
    bar = {"open": 11.0, "vol": 10000, "code": "600000.SH"}
    ok, why = EX.can_buy_at_open(bar, prev_close=10.0)
    assert not ok and why == "limit_up_open", (ok, why)


def test_can_buy_normal():
    bar = {"open": 10.5, "vol": 10000, "code": "600000.SH"}
    ok, why = EX.can_buy_at_open(bar, prev_close=10.0)
    assert ok, why


def test_can_buy_suspended():
    """无 bar 或 vol=0 视为停牌，买不进。"""
    assert EX.can_buy_at_open(None, 10.0)[0] is False
    bar = {"open": 10.5, "vol": 0, "code": "600000.SH"}
    assert EX.can_buy_at_open(bar, 10.0)[0] is False


def test_can_sell_limit_down_open():
    """一字跌停卖不掉。"""
    bar = {"open": 9.0, "vol": 10000, "code": "600000.SH"}
    ok, why = EX.can_sell_at_open(bar, prev_close=10.0)
    assert not ok and why == "limit_down_open", (ok, why)


def test_exit_stop_loss():
    cfg = {"hold_days": 15, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    bar = {"open": 9.2, "high": 9.3}
    should, reason, _ = EX.check_exit(pos, bar, held_days=3, cfg=cfg, peak=10.0)
    assert should and reason == "STOP", (should, reason)


def test_exit_take_profit():
    cfg = {"hold_days": 15, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    bar = {"open": 11.6, "high": 11.8}
    should, reason, _ = EX.check_exit(pos, bar, held_days=3, cfg=cfg, peak=11.8)
    assert should and reason == "TP", (should, reason)


def test_exit_mature_beats_everything():
    """持有期满优先于止损止盈判定。"""
    cfg = {"hold_days": 5, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    bar = {"open": 9.0, "high": 9.0}
    should, reason, _ = EX.check_exit(pos, bar, held_days=5, cfg=cfg, peak=10.0)
    assert should and reason == "MATURE", (should, reason)


def test_exit_t_plus_1_blocked():
    """买入当日不可卖（held_days=0）。"""
    cfg = {"hold_days": 15, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    bar = {"open": 8.0, "high": 8.0}
    should, reason, _ = EX.check_exit(pos, bar, held_days=0, cfg=cfg, peak=10.0)
    assert not should, (should, reason)


def test_trailing_not_activated_below_threshold():
    """峰值未达激活阈值时不追盈（防"追盈=追跌"）。"""
    cfg = {"hold_days": 15, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    # 峰值 10.5（+5% < 8% 激活线），当前 9.7（-3%，未触发 -7% 止损）
    bar = {"open": 9.7, "high": 9.7}
    should, reason, _ = EX.check_exit(pos, bar, held_days=3, cfg=cfg, peak=10.5)
    assert not should, (should, reason)


def test_trailing_triggered_after_activation():
    cfg = {"hold_days": 15, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    # 峰值 12.0（+20% 已激活），回撤到 11.0 = 峰值×(1-8%)=11.04 之下
    bar = {"open": 11.0, "high": 11.0}
    should, reason, _ = EX.check_exit(pos, bar, held_days=3, cfg=cfg, peak=12.0)
    assert should and reason == "TRAIL", (should, reason)


def test_trailing_breakeven_floor():
    """激活后触发线不低于成本价（峰值×(1-8%) 低于成本时按成本保护）。"""
    cfg = {"hold_days": 15, "stop_loss": -0.07, "take_profit": 0.15,
           "trailing_pct": 0.08, "trailing_activate_pct": 0.08}
    pos = {"buy_px": 10.0}
    # 峰值 10.85（+8.5% 刚激活），峰值×0.92 = 9.98 < 成本 10.0
    bar = {"open": 9.99, "high": 9.99}
    should, reason, _ = EX.check_exit(pos, bar, held_days=3, cfg=cfg, peak=10.85)
    # 9.99 < max(10.0, 9.98) = 10.0 → 触发 TRAIL
    assert should and reason == "TRAIL", (should, reason)


def test_calc_buy_volume():
    vol, amt = EX.calc_buy_volume(33000, 15.30)
    assert vol == 2100, vol
    assert abs(amt - 2100 * 15.30) < 1e-6
    # 预算不足一手
    assert EX.calc_buy_volume(1000, 15.30) == (0, 0.0)


def _run_all():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in fns:
        try:
            fn()
            passed += 1
            print("  PASS  %s" % name)
        except AssertionError as e:
            failed += 1
            print("  FAIL  %s  ->  %s" % (name, e))
        except Exception as e:
            failed += 1
            print("  ERROR %s  ->  %r" % (name, e))
    print("\n%d passed, %d failed, %d total" % (passed, failed, len(fns)))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_all())
