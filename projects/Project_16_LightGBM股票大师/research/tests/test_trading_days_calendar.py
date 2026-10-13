# -*- coding: utf-8 -*-
"""持有期交易日计数·法定节假日回归测试（T-20261008-004）。

背景（2026-10-08 实盘事故）：
  rebalance_daily.py::_trading_days_between 旧实现「只跳周末、忽略法定假日」，
  把国庆 10-01~10-07 计成 5 个交易日 → 09-30 买入、真实仅持有 1 个交易日的 5 只
  持仓被判 MATURE 全部清仓（毛利 +1,039 / 净 +916 落袋，本应继续持有）。
  每逢春节 / 国庆后首个交易日必复发。

修复分两轮：
  第一轮（T-20261008-004）：函数主体改接权威日历 data/ashare_trade_dates.txt。
  第二轮（本次收口）：**日历缺失时的回退分支仍是「只跳周末」**，等于把事故逻辑
  原样留在 fail-open 路径上——日历文件一旦缺失/损坏即复发。现统一为逐日过
  is_trade_day（周末 → 权威日历 → 法定节假日表兜底），与 rebalance_g2.py /
  rebalance_ens.py 的 _count_trade_days 完全同口径，三臂持有期语义一致可比。

用例：
  C1 事故复现   ：09-30 → 10-08 持有交易日必须 = 1（旧口径 = 6），且 < HOLD_DAYS
  C2 节后第二日 ：09-30 → 10-09 = 2（修前因日历未覆盖今日而少计为 1）
  C3 中秋       ：09-24 → 09-28 = 1（09-25~27 休市）
  C4 春节       ：02-13 → 02-24 = 1（02-15~23 休市）
  C5 正常周不误伤：09-14 → 09-18 = 4、09-14 → 09-21 = 5、同日/逆序 = 0
  C6 日历缺失不复发：强制 cal=None 时节假日仍判非交易日（防回退到旧逻辑）
  C7 三臂同口径 ：与 is_trade_day 逐日判定结果逐例一致
  C8 源码绊线   ：函数与全文件内不得残留 weekday()<5 式周末近似

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_trading_days_calendar.py
"""
import inspect
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

import is_trade_day as ITD          # noqa: E402
import rebalance_daily as R         # noqa: E402

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


def _d(s):
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _old_weekend_only(d0, d1):
    """旧实现（事故口径）：只跳周末、忽略法定假日。"""
    n, cur, one = 0, d0, timedelta(days=1)
    while cur < d1:
        cur += one
        if cur.weekday() < 5:
            n += 1
    return n


def test_c1_incident_holiday_week_not_counted():
    """C1 事故复现：国庆周绝不能被计入持有期。"""
    d0, d1 = _d("20260930"), _d("20261008")
    got = R._trading_days_between(d0, d1)
    old = _old_weekend_only(d0, d1)
    _check("C1a 09-30→10-08 持有交易日 = 1（事故真值）", got == 1, "got=%s" % got)
    _check("C1b 旧口径算成 6（事故根因可复现）", old == 6, "old=%s" % old)
    _check("C1c 未满 HOLD_DAYS=%d → 不判 MATURE" % R.HOLD_DAYS,
           got < R.HOLD_DAYS, "got=%s" % got)


def test_c2_post_holiday_second_day_counted():
    """C2 节后第二日应计入（修前日历未覆盖今日 → 少计 1 天）。"""
    got = R._trading_days_between(_d("20260930"), _d("20261009"))
    _check("C2 09-30→10-09 = 2", got == 2, "got=%s" % got)


def test_c3_midautumn():
    """C3 中秋 09-25~27 休市。"""
    got = R._trading_days_between(_d("20260924"), _d("20260928"))
    _check("C3 09-24→09-28 = 1（09-25 周五休市）", got == 1, "got=%s" % got)


def test_c4_spring_festival():
    """C4 春节 02-15~23 休市。"""
    got = R._trading_days_between(_d("20260213"), _d("20260224"))
    _check("C4 02-13→02-24 = 1（春节长假）", got == 1, "got=%s" % got)


def test_c5_normal_week_not_broken():
    """C5 正常周/跨周末/边界不误伤。"""
    a = R._trading_days_between(_d("20260914"), _d("20260918"))
    b = R._trading_days_between(_d("20260914"), _d("20260921"))
    c = R._trading_days_between(_d("20260914"), _d("20260914"))
    e = R._trading_days_between(_d("20260921"), _d("20260914"))
    _check("C5a 正常周 09-14→09-18 = 4", a == 4, "got=%s" % a)
    _check("C5b 跨周末 09-14→09-21 = 5", b == 5, "got=%s" % b)
    _check("C5c 同日 = 0 且逆序 = 0", c == 0 and e == 0, "same=%s rev=%s" % (c, e))


def test_c6_missing_calendar_no_regression():
    """C6 关键：日历缺失（cal=None）时不得回退到「只跳周末」旧逻辑。"""
    saved = (R._CAL_LOADED, R._CAL_CACHE)
    try:
        R._CAL_LOADED, R._CAL_CACHE = True, None    # 强制 _get_trade_calendar() 返回 None
        got = R._trading_days_between(_d("20260930"), _d("20261008"))
        _check("C6a 日历缺失时 09-30→10-08 仍 = 1（旧回退会算成 6）", got == 1, "got=%s" % got)
        _check("C6b 日历缺失时国庆日判非交易日",
               R._is_trade_day(_d("20261005"), set()) is False)
        _check("C6c 日历缺失时非节假日工作日判交易日",
               R._is_trade_day(_d("20261009"), set()) is True)
        _check("C6d 日历缺失时周末判非交易日",
               R._is_trade_day(_d("20261010"), set()) is False)
    finally:
        R._CAL_LOADED, R._CAL_CACHE = saved


def test_c7_same_semantics_as_g2_ens():
    """C7 与 is_trade_day（G2/ENS 的 _count_trade_days 所用）逐例一致。"""
    cal = ITD.load_calendar()
    for s0, s1 in [("20260930", "20261008"), ("20260924", "20260928"),
                   ("20260914", "20260921"), ("20260213", "20260224")]:
        d0, d1 = _d(s0), _d(s1)
        n, cur, one = 0, d0, timedelta(days=1)
        while cur < d1:
            cur += one
            if ITD.is_trade_day(cur, cal).get("is_trade_day"):
                n += 1
        got = R._trading_days_between(d0, d1)
        _check("C7 %s→%s 与 is_trade_day 一致 = %d" % (s0, s1, n), got == n, "got=%s" % got)


def test_c8_source_tripwire():
    """C8 源码绊线：防未来重构把周末近似改回来。"""
    src = inspect.getsource(R._trading_days_between)
    _check("C8a 函数内无 weekday() 周末近似", "weekday()" not in src, src)
    _check("C8b 函数经 _is_trade_day 逐日判定", "_is_trade_day(" in src, src)
    src2 = inspect.getsource(R._is_trade_day)
    _check("C8c _is_trade_day 接 is_trade_day 权威口径", "ITD.is_trade_day" in src2, src2)
    whole = open(os.path.join(PROJECT_ROOT, "rebalance_daily.py"), encoding="utf-8").read()
    _check("C8d 全文件无残留只跳周末计数",
           "weekday() < 5" not in whole and "weekday()<5" not in whole)


def test_c9_missing_holiday_year_fails_loud():
    """C9 跨年 fail-loud：年份未登记 HOLIDAYS 时必须可见，不得静默按交易日处理。

    HOLIDAYS 只硬编码到 2026；2027 起若未补表，长假会被判成「默认交易日」→
    门禁在休市日误跑 + 持有期把长假计成交易日 → MATURE 提前触发（10-08 同型）。
    """
    import contextlib
    import io

    saved = set(ITD._WARNED_YEARS)
    try:
        ITD._WARNED_YEARS.clear()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            r1 = ITD.is_trade_day(date(2027, 1, 1), set())
            r2 = ITD.is_trade_day(date(2027, 1, 4), set())
        err = buf.getvalue()
        _check("C9a 未登记年份 reason 带告警标注",
               "2027年休市表未登记" in r1.get("reason", ""), r1)
        _check("C9b stderr 有 fail-loud 提示",
               "HOLIDAYS 未登记 2027" in err, err)
        _check("C9c 每年只提示一次（不刷屏）",
               err.count("HOLIDAYS 未登记 2027") == 1, err.count("HOLIDAYS 未登记 2027"))
        _check("C9d 保持原行为=按交易日（改成停摆更糟）",
               r1.get("is_trade_day") is True and r2.get("is_trade_day") is True, (r1, r2))

        buf2 = io.StringIO()
        with contextlib.redirect_stderr(buf2):
            r26 = ITD.is_trade_day(date(2026, 10, 9), set())
        _check("C9e 有表年份不误报",
               "未登记" not in r26.get("reason", "") and buf2.getvalue() == "",
               (r26, buf2.getvalue()))
    finally:
        ITD._WARNED_YEARS.clear()
        ITD._WARNED_YEARS.update(saved)


if __name__ == "__main__":
    print("== 持有期交易日计数·法定节假日回归测试（T-20261008-004）==")
    test_c1_incident_holiday_week_not_counted()
    test_c2_post_holiday_second_day_counted()
    test_c3_midautumn()
    test_c4_spring_festival()
    test_c5_normal_week_not_broken()
    test_c6_missing_calendar_no_regression()
    test_c7_same_semantics_as_g2_ens()
    test_c8_source_tripwire()
    test_c9_missing_holiday_year_fails_loud()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
