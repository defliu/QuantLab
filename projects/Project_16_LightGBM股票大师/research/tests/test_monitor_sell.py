# -*- coding: utf-8 -*-
"""qmt_monitor 自动卖出记账逻辑单元测试（T-20260909 方案A）。

覆盖三个关键分支（不连真 QMT，mock order_guard 返回值）：
  1) FILLED 全部成交 → 记账 + sold.add（不再评估）
  2) CANCELED_TIMEOUT 部分成交（ok=False 但 traded_vol>0）→ 按真实量记账、不 sold.add（剩余下轮再卖）
  3) LIMIT_SKIP 涨跌停/0 成交 → 绝不记账、不 sold.add（下轮继续评估）

验证目标：删 PK_OUT/接入 order_guard 后，风控卖出的"成交记账"只认实际成交量，
不再出现"下单即记成交"（2026-09-09 300413 挂单被误记成交的复现防护）。

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_monitor_sell.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

import qmt_monitor as M  # noqa: E402

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


def _run_sell_scenario(r_result, rows_log):
    """执行一次自动卖出决策（mock _sell 返回值），返回 (sold_set, sig, rows)。"""
    # mock C.append_trade_rows：捕获记账行，绝不写真实 qmt_trade_log.csv
    M.C.append_trade_rows = lambda rows: rows_log.append(rows[0])

    # 构造触发 STOP 的 tick（现价 19.44 < 止损位 20.45）；evaluate 用 dict 风格 tick.get
    action, note = M.evaluate(21.99, {"lastPrice": 19.44, "high": 20.0}, 21.99, 2000)
    _check("信号判定为 SELL_STOP", action == "SELL_STOP", action)

    sig = {"code": "300413.SZ", "action": action, "note": note, "last_price": 19.44,
           "cost": 21.99, "time": "2026-09-09 09:45:00"}
    sold = set()

    # 复刻 main() 内 auto_sell 分支的决策逻辑（与 qmt_monitor.py L378-404 保持同步）
    sell_vol = min(2000, 2000)
    r = r_result
    if r["traded_vol"] > 0:
        sig["auto_sold"] = True
        sig["order_id"] = r["order_id"]
        sig["traded_vol"] = r["traded_vol"]
        M.C.append_trade_rows([[sig["time"], "300413.SZ", "SELL",
                                r["traded_vol"], 19.44, action, r["order_id"]]])
        if r["ok"]:
            sold.add("300413.SZ")
    else:
        sig["auto_sold"] = False
        sig["order_note"] = r["note"]
    return sold, sig, rows_log


def test_filled_all():
    """FILLED 全部成交 → 记账 2000 股 + sold 标记（不再评估）。"""
    rows_log = []
    sold, sig, rows = _run_sell_scenario(
        {"ok": True, "action": "FILLED", "traded_vol": 2000, "order_id": 123,
         "attempts": 1, "note": "全部成交 2000股"}, rows_log)
    _check("FILLED: sold 标记", "300413.SZ" in sold)
    _check("FILLED: 记账 1 条", len(rows) == 1, len(rows))
    _check("FILLED: 记真实量 2000", len(rows) == 1 and rows[0][3] == 2000, rows)
    _check("FILLED: signal auto_sold=True",
           sig["auto_sold"] is True and sig["traded_vol"] == 2000)


def test_partial_timeout():
    """部分成交（ok=False 但 traded_vol=800）→ 记 800，不 sold.add（剩余 1200 下轮再卖）。"""
    rows_log = []
    sold, sig, rows = _run_sell_scenario(
        {"ok": False, "action": "CANCELED_TIMEOUT", "traded_vol": 800, "order_id": 456,
         "attempts": 5, "note": "重试 5 次未全成（已成交 800股）"}, rows_log)
    _check("PARTIAL: 不 sold.add（剩余待卖）", "300413.SZ" not in sold)
    _check("PARTIAL: 记账 1 条", len(rows) == 1, len(rows))
    _check("PARTIAL: 只记真实量 800", len(rows) == 1 and rows[0][3] == 800, rows)
    _check("PARTIAL: signal auto_sold=True traded=800",
           sig["auto_sold"] is True and sig["traded_vol"] == 800)


def test_limit_skip():
    """涨跌停跳过（0 成交）→ 绝不记账、不 sold.add，下轮继续评估。"""
    rows_log = []
    sold, sig, rows = _run_sell_scenario(
        {"ok": False, "action": "LIMIT_SKIP", "traded_vol": 0, "order_id": -1,
         "attempts": 1, "note": "现价≤跌停，卖单无法成交"}, rows_log)
    _check("LIMIT_SKIP: 不 sold.add", "300413.SZ" not in sold)
    _check("LIMIT_SKIP: 绝不记账（防假成交）", len(rows) == 0, len(rows))
    _check("LIMIT_SKIP: auto_sold=False", sig["auto_sold"] is False)
    _check("LIMIT_SKIP: 附跳过节原因", "跌停" in sig["order_note"])


def test_rejected():
    """废单（0 成交）→ 不记账、不 sold.add。"""
    rows_log = []
    sold, sig, rows = _run_sell_scenario(
        {"ok": False, "action": "REJECTED", "traded_vol": 0, "order_id": -1,
         "attempts": 2, "note": "委托废单"}, rows_log)
    _check("REJECTED: 不 sold.add", "300413.SZ" not in sold)
    _check("REJECTED: 不记账", len(rows) == 0, len(rows))
    _check("REJECTED: auto_sold=False", sig["auto_sold"] is False)


if __name__ == "__main__":
    print("== qmt_monitor 自动卖出记账逻辑单元测试（T-20260909 方案A）==")
    _orig_append = M.C.append_trade_rows  # 备份，测试结束后恢复，防污染
    try:
        test_filled_all()
        test_partial_timeout()
        test_limit_skip()
        test_rejected()
    finally:
        M.C.append_trade_rows = _orig_append
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
