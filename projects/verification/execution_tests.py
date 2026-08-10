# coding=utf-8
"""
回测引擎修复专项单测（execution.py + analyzer.py）
====================================================
直接 import 引擎纯函数，用合成数据验证 2026-08-10 修复：

  E-1  容量约束：max_adv_pct>0 且买入金额超前一交易日成交额上限 → 整单拒绝
  E-2  容量约束关闭（max_adv_pct=0）→ 基线不变，正常成交
  E-3  win_rate 计入期末未平仓持仓（open_positions 传入）
  E-4  _trading_day_diff 日期缺失兜底（不再恒为 0）

运行：
    python projects/verification/execution_tests.py
"""
import os
import sys
from pathlib import Path

import pandas as pd
import numpy as np

# 保证能 import backtest.* （本文件位于 projects/verification/ 下）
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest.execution import fill_buy, fill_sell, _lot_floor, _price_limit  # noqa: E402
from backtest.analyzer import compute_metrics, _trading_day_diff  # noqa: E402

OUTPUT_DIR = str(_HERE / "results")


def _window(df_rows):
    """Build market_window {code: DataFrame} with sorted string dates."""
    df = pd.DataFrame(df_rows)
    df = df.sort_values("date").reset_index(drop=True)
    return {"000001.SZ": df}


def _exec_cfg(max_adv_pct=0.0):
    return {
        "price": "next_open",
        "slippage": 0.001,
        "commission_rate": 0.00025,
        "tax_rate": 0.0001,
        "max_adv_pct": max_adv_pct,
    }


def test_e1_capacity_exceeded():
    """E-1: 买入金额超过前一交易日成交额×max_adv_pct → 整单拒绝。"""
    mw = _window([
        # prev(day0) 成交额 100 万；day1 开盘 10 元
        {"date": "2024-01-02", "open": 9.8, "high": 10.2, "low": 9.7,
         "close": 10.0, "vol": 100000, "amount": 1000000.0, "is_st": False},
        {"date": "2024-01-03", "open": 10.0, "high": 10.5, "low": 9.9,
         "close": 10.4, "vol": 100000, "amount": 1000000.0, "is_st": False},
    ])
    cand = {"code": "000001.SZ", "target_cash": 200000.0, "reason": "top"}
    # max_adv_pct=0.10 → 容量上限 = 100万 × 0.10 = 10万 < 20万 → 拒绝
    trade, reason = fill_buy(cand, mw, "2024-01-03", _exec_cfg(0.10), "rid")
    ok = (trade is None) and (reason == "capacity_exceeded")
    print("[E-1] 容量超限拒绝: trade=%s reason=%s -> %s" % (trade, reason, "PASS" if ok else "FAIL"))
    return {"test": "E-1 容量约束超限拒绝", "pass": "PASS" if ok else "FAIL",
            "result": "reason=%s" % reason}


def test_e2_capacity_off_baseline():
    """E-2: max_adv_pct=0（默认）→ 不拦截，正常成交。"""
    mw = _window([
        {"date": "2024-01-02", "open": 9.8, "high": 10.2, "low": 9.7,
         "close": 10.0, "vol": 100000, "amount": 1000000.0, "is_st": False},
        {"date": "2024-01-03", "open": 10.0, "high": 10.5, "low": 9.9,
         "close": 10.4, "vol": 100000, "amount": 1000000.0, "is_st": False},
    ])
    cand = {"code": "000001.SZ", "target_cash": 200000.0, "reason": "top"}
    trade, reason = fill_buy(cand, mw, "2024-01-03", _exec_cfg(0.0), "rid")
    # 200000 / (10 * 1.001) ≈ 19980 → 19900 股，正常成交
    ok = (trade is not None) and (reason is None) and (trade["volume"] == 19900)
    print("[E-2] 容量关闭基线: trade vol=%s reason=%s -> %s"
          % (trade["volume"] if trade else None, reason, "PASS" if ok else "FAIL"))
    return {"test": "E-2 容量约束默认关闭", "pass": "PASS" if ok else "FAIL",
            "result": "vol=%s" % (trade["volume"] if trade else None)}


def test_e3_open_position_win_rate():
    """E-3: open_positions 传入时，未平仓买单按期末价计入胜率/持有期。"""
    cal = ["2024-01-02", "2024-01-03"]
    equity = [
        {"date": "2024-01-02", "total_asset": 1000000.0, "daily_return": 0.0},
        {"date": "2024-01-03", "total_asset": 1010000.0, "daily_return": 0.01},
    ]
    trades = [
        {"code": "A", "side": "buy", "date": "2024-01-02", "volume": 1000,
         "price": 10.0, "amount": 10000.0, "commission": 2.5, "tax": 0.0},
    ]
    open_pos = [{"code": "A", "last_price": 11.0, "volume": 1000, "cost_price": 10.0}]

    # 传入 open_positions → 期末价 11 > 成本 → 计入胜
    perf = compute_metrics(equity, trades, cal, 1000000.0,
                           open_positions=open_pos)
    ok = (abs(perf["win_rate"] - 1.0) < 1e-9) and (perf["n_open"] == 1) \
        and (perf["avg_holding_days"] == 1.0)

    # 不传 open_positions → 未平仓被忽略（向后兼容，win_rate=0）
    perf_legacy = compute_metrics(equity, trades, cal, 1000000.0)
    ok = ok and (perf_legacy["win_rate"] == 0.0)

    print("[E-3] 未平仓计入胜率: win_rate=%.3f n_open=%d avg_hold=%.1f legacy_win=%.3f -> %s"
          % (perf["win_rate"], perf["n_open"], perf["avg_holding_days"],
             perf_legacy["win_rate"], "PASS" if ok else "FAIL"))
    return {"test": "E-3 win_rate 计入未平仓", "pass": "PASS" if ok else "FAIL",
            "result": "win_rate=%.3f n_open=%d" % (perf["win_rate"], perf["n_open"])}


def test_e4_trading_day_diff_fallback():
    """E-4: 日期不在日历时兜底为整个样本期（不再恒为 0）。"""
    cal = ["2024-01-02", "2024-01-03", "2024-01-04"]
    d = _trading_day_diff("1999-01-01", "2024-01-04", cal)  # 首日不在日历
    ok = d == len(cal)
    # 正常路径仍精确
    ok = ok and (_trading_day_diff("2024-01-02", "2024-01-04", cal) == 2)
    print("[E-4] 日期缺失兜底: fallback=%d (期望 %d), 正常路径=2 -> %s"
          % (d, len(cal), "PASS" if ok else "FAIL"))
    return {"test": "E-4 日期缺失兜底", "pass": "PASS" if ok else "FAIL",
            "result": "fallback=%d" % d}


def test_e5_limit_lot_smoke():
    """E-5: 回归烟测 — 涨跌停/停牌/整数手在重构后仍正常。"""
    # 涨停开盘买入被拒
    mw = _window([
        {"date": "2024-01-02", "open": 10.0, "high": 11.0, "low": 9.9,
         "close": 11.0, "vol": 100000, "amount": 1000000.0, "is_st": False},
        {"date": "2024-01-03", "open": 12.1, "high": 12.1, "low": 12.0,
         "close": 12.1, "vol": 100000, "amount": 1000000.0, "is_st": False},  # +10% 涨停
    ])
    cand = {"code": "000001.SZ", "target_cash": 200000.0, "reason": "top"}
    trade, reason = fill_buy(cand, mw, "2024-01-03", _exec_cfg(0.0), "rid")
    ok1 = (trade is None) and (reason == "limit_up_at_open")

    # 停牌（无当日 bar）→ unfilled=suspended
    mw2 = _window([
        {"date": "2024-01-02", "open": 10.0, "high": 10.0, "low": 10.0,
         "close": 10.0, "vol": 1000, "amount": 10000.0, "is_st": False},
    ])
    trade, reason = fill_buy(cand, mw2, "2024-01-03", _exec_cfg(0.0), "rid")
    ok2 = (trade is None) and (reason == "suspended")

    # 整数手向下取整
    ok3 = _lot_floor(199) == 100 and _lot_floor(50) == 0 and _lot_floor(250) == 200
    # 双创 20% 涨停幅度
    ok4 = _price_limit("300001.SZ") == 0.20 and _price_limit("000001.SZ") == 0.10

    ok = ok1 and ok2 and ok3 and ok4
    print("[E-5] 涨跌停/停牌/整数手烟测: limit=%s suspended=%s lot=%s price_limit=%s -> %s"
          % (ok1, ok2, ok3, ok4, "PASS" if ok else "FAIL"))
    return {"test": "E-5 涨跌停/停牌/整数手回归", "pass": "PASS" if ok else "FAIL",
            "result": "limit=%s suspended=%s lot=%s pl=%s" % (ok1, ok2, ok3, ok4)}


def run_all_tests():
    print("=" * 60)
    print("回测引擎修复专项单测（execution + analyzer）")
    print("=" * 60)
    results = [
        test_e1_capacity_exceeded(),
        test_e2_capacity_off_baseline(),
        test_e3_open_position_win_rate(),
        test_e4_trading_day_diff_fallback(),
        test_e5_limit_lot_smoke(),
    ]
    print("\n" + "=" * 60)
    print("修复专项单测汇总")
    print("=" * 60)
    passed = sum(1 for r in results if r["pass"] == "PASS")
    for r in results:
        print("  %s: %s" % (r["test"], r["pass"]))
    print("\n通过: %d/%d" % (passed, len(results)))

    out = Path(OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    with open(str(out / "execution_test_report.md"), "w", encoding="utf-8") as f:
        f.write("# 回测引擎修复专项单测报告\n\n")
        f.write("## 结果: %d/%d 通过\n\n" % (passed, len(results)))
        f.write("| 测试项 | 结果 | 说明 |\n|---|---|---|\n")
        for r in results:
            f.write("| %s | %s | %s |\n" % (r["test"], r["pass"], r["result"]))
    print("报告已保存: %s" % (out / "execution_test_report.md"))
    return results


if __name__ == "__main__":
    run_all_tests()
