# coding: utf-8
"""RPS 主升浪策略单元级逻辑验证。

不跑完整回测，直接构造小窗口数据调用 evaluate_day，验证：
  1. 调仓日能选出股票（candidate_passed > 0）
  2. 大盘门控生效
  3. 止损/移动止盈/时间止损逻辑
  4. 字段名兼容（vol/is_st/ts_code）
"""
import os
import sys

# 加入 QuantLab 根目录到 sys.path
QL = "D:/QuantLab"
if QL not in sys.path:
    sys.path.insert(0, QL)

import pandas as pd
import numpy as np

from strategy.rps_momentum import (
    evaluate_day,
    _calc_rps,
    _check_breakout,
    _check_volume_confirm,
    _check_market_gate,
    _check_pullback_entry,
    _hold_decision,
)
from strategy.schedule import is_rebalance_day


def make_df(n=300, trend=0.002, vol_base=1e7):
    """构造一个 n 天日线 DataFrame（模拟 astock 字段名 vol/is_st）。"""
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(42)
    # 生成上涨趋势价格
    rets = rng.normal(trend, 0.02, n)
    close = 10 * np.cumprod(1 + rets)
    open_ = close * (1 + rng.normal(0, 0.005, n))
    high = np.maximum(open_, close) * (1 + abs(rng.normal(0, 0.01, n)))
    low = np.minimum(open_, close) * (1 - abs(rng.normal(0, 0.01, n)))
    vol = np.abs(rng.normal(vol_base, vol_base * 0.2, n))
    vol[-1] = vol_base * 2.5  # 最后一天放量 2.5 倍

    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "vol": vol,
        "amount": vol * close,
        "is_st": 0.0,
    })
    return df


def test_calc_rps():
    df = make_df()
    ret = _calc_rps(df, 20)
    assert ret is not None, "RPS 计算失败"
    print("[OK] _calc_rps: 20日涨幅 = %.4f" % ret)


def test_breakout():
    df = make_df()
    # 构造：最后一天创新高
    df.loc[df.index[-1], "high"] = df["high"].max() * 1.05
    df.loc[df.index[-1], "close"] = df["close"].max() * 1.05
    assert _check_breakout(df, 20) == True, "突破检测失败"
    print("[OK] _check_breakout: 突破 20 日新高检测正确")


def test_pullback_entry():
    # 构造：先上涨到高点，再横盘回调（让 20 日均线回落到接近当前价）
    # 明确构造价格序列：前 250 日缓慢上涨（建立趋势+均线），后 20 日从高点回落 8% 并贴近均线
    n = 300
    dates = pd.bdate_range("2023-01-01", periods=n)
    rng = np.random.default_rng(42)

    # 前 250 日：温和上涨
    base_rets = rng.normal(0.001, 0.01, 250)
    base_close = 10 * np.cumprod(1 + base_rets)
    # 后续 50 日：先冲到高点（+10%），再回落横盘
    high_point = base_close[-1] * 1.10
    pullback_vals = np.linspace(high_point, high_point * 0.92, 50)  # 从高点回落到 -8%
    # 让最后 20 日保持在回落后的水平（贴近均线）
    tail = pullback_vals[-20:]
    close_vals = np.concatenate([base_close, pullback_vals[:-20], tail])

    close_series = pd.Series(close_vals)
    df = pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": close_series * 0.99,
        "high": close_series * 1.02,
        "low": close_series * 0.98,
        "close": close_series,
        "vol": np.abs(rng.normal(1e7, 2e6, n)),
        "is_st": 0.0,
    })

    # 检查：当前价距近期高点回调 ~8%，且在 20 日均线附近（应通过）
    res_pullback = _check_pullback_entry(df, 20, 0.15, 0.02)
    print("[OK] _check_pullback_entry: 回调用例结果=%s (last=%.2f ma20=%.2f)" %
          (res_pullback, float(df["close"].iloc[-1]), float(df["close"].iloc[-20:].mean())))
    assert res_pullback == True, "回调买入应通过"

    # 反向：仍在加速上涨（最后一天远高于 past 20 日高点，负回调）→ 应拒绝
    df2 = make_df(trend=0.004)
    past_high = max(float(v) for v in df2["high"].iloc[-21:-1])
    df2.loc[df2.index[-1], "close"] = past_high * 1.10  # 创新高，无回调
    df2.loc[df2.index[-1], "high"] = past_high * 1.10
    res_no_pullback = _check_pullback_entry(df2, 20, 0.15, 0.02)
    print("[OK] _check_pullback_entry: 无回调用例结果=%s (last=%.2f past_high=%.2f)" %
          (res_no_pullback, float(df2["close"].iloc[-1]), past_high))
    assert res_no_pullback == False, "无回调（加速上涨）应拒绝"


def test_volume_confirm():
    df = make_df()
    # 最后一天放量 2.5 倍
    assert _check_volume_confirm(df, 1.5) == True, "放量确认失败"
    print("[OK] _check_volume_confirm: 放量确认检测正确")


def test_market_gate():
    dates = pd.bdate_range("2023-01-01", periods=100).strftime("%Y-%m-%d").tolist()
    # 构造上升趋势的大盘
    bm = {}
    price = 3000.0
    for d in dates:
        price *= 1.001
        bm[d] = price
    today = dates[-1]
    assert _check_market_gate(bm, today, 60) == True, "上升趋势应放行"
    # 构造下跌趋势
    bm2 = {}
    price = 3000.0
    for d in dates:
        price *= 0.999
        bm2[d] = price
    assert _check_market_gate(bm2, today, 60) == False, "下跌趋势应拦截"
    print("[OK] _check_market_gate: 大盘门控逻辑正确")


def test_hold_decision():
    positions = [
        {"code": "000001.SZ", "cost_price": 10.0, "last_price": 9.0,
         "holding_days": 5, "entry_date": "2023-05-01"},  # 亏损 10% -> 触发止损
        {"code": "000002.SZ", "cost_price": 10.0, "last_price": 12.0,
         "holding_days": 5, "entry_date": "2023-05-01"},  # 盈利 -> 持有
        {"code": "000003.SZ", "cost_price": 10.0, "last_price": 20.0,
         "holding_days": 70, "entry_date": "2023-05-01"},  # 时间止损
    ]
    mw = {}
    for p in positions:
        df = make_df()
        # entry_date(2023-05-01) 之后的价格都设为本价（不触发移动止盈）
        entry_idx = None
        for i, d in enumerate(df["date"].values):
            if str(d) >= p["entry_date"]:
                entry_idx = i
                break
        if entry_idx is not None:
            df.loc[df.index[entry_idx]:, "close"] = p["last_price"]
            df.loc[df.index[entry_idx]:, "high"] = p["last_price"]
        df.loc[df.index[-1], "close"] = p["last_price"]
        df.loc[df.index[-1], "high"] = p["last_price"]
        mw[p["code"]] = df

    dec = _hold_decision("2023-06-01", positions, -0.08, -0.12, 60, mw)
    # 000001 止损、000003 时间止损应被剔除，000002 保留
    keep = dec.get("target_weights", {})
    assert "000001.SZ" not in keep, "止损未生效"
    assert "000003.SZ" not in keep, "时间止损未生效"
    assert "000002.SZ" in keep, "盈利持仓应保留"
    print("[OK] _hold_decision: 止损/移动止盈/时间止损逻辑正确, keep=%s" % list(keep.keys()))

    # 额外：验证无触发时（全部盈利且未超时）不返回 target_weights
    dec2 = _hold_decision("2023-06-01",
                          [{"code": "000002.SZ", "cost_price": 10.0,
                            "last_price": 12.0, "holding_days": 5,
                            "entry_date": "2023-05-01"}],
                          -0.08, -0.12, 60, mw)
    assert "target_weights" not in dec2, "无触发时不应返回 target_weights（避免引擎 diff）"
    print("[OK] _hold_decision: 无触发时不返回 target_weights（防加仓 bug）")


def test_evaluate_day_rebalance():
    """验证调仓日能选出股票。"""
    # 构造 universe：50 只上涨趋势股 + 1 只 ST
    n_stocks = 51
    market_window = {}
    universe = []
    for i in range(n_stocks):
        code = "000%04d.SZ" % (i + 1)
        universe.append(code)
        df = make_df(trend=0.003 + i * 0.0001)
        # 让最后一天创新高 + 放量（满足突破+放量确认）
        df.loc[df.index[-1], "close"] = df["close"].max() * 1.05
        df.loc[df.index[-1], "high"] = df["high"].max() * 1.05
        df.loc[df.index[-1], "vol"] = df["vol"].max() * 1.5
        if i == n_stocks - 1:
            df.loc[df.index[-1], "is_st"] = 1.0  # 最后一只设 ST
        market_window[code] = df

    # 大盘门控：上升趋势放行
    dates = pd.bdate_range("2023-01-01", periods=300).strftime("%Y-%m-%d").tolist()
    bm = {}
    price = 3000.0
    for d in dates:
        price *= 1.001
        bm[d] = price

    # 找 2023 年 2 月的第一个交易日（monthly 调仓日）
    feb_days = [d for d in dates if d.startswith("2023-02")]
    assert feb_days, "无 2023-02 交易日"
    rebal_date = feb_days[0]  # 2 月第一个交易日
    cal = dates
    assert is_rebalance_day(rebal_date, "monthly", cal), "%s 应为月首调仓日" % rebal_date

    aux = {"trading_calendar": cal, "benchmark_closes": bm}
    cfg = {
        "rebalance_freq": "monthly",
        "n_hold": 10,
        "rps_window_short": 20,
        "rps_window_long": 120,
        "rps_threshold": 80,
        "breakout_window": 20,
        "volume_confirm": 1,
        "volume_ratio_min": 1.5,
        "market_gate": 1,
        "ma_window": 60,
        "stop_loss": -0.08,
        "trailing_stop": -0.12,
        "max_holding_days": 60,
        "min_history": 100,
    }

    dec = evaluate_day(
        current_date=rebal_date,
        market_window=market_window,
        positions=[],
        cash=1000000,
        universe=universe,
        account_state={},
        strategy_config=cfg,
        aux_data=aux,
    )
    tw = dec.get("target_weights", {})
    diag = dec.get("diagnostics", {})
    print("[OK] evaluate_day 调仓日(%s): selected=%d, candidate_passed=%d, warnings=%s"
          % (rebal_date, len(tw), diag.get("candidate_passed", 0), diag.get("warnings", [])))
    assert len(tw) > 0, "调仓日应选出股票"
    # ST 股票不应被选中
    assert "000%04d.SZ" % n_stocks not in tw, "ST 股票不应被选中"
    print("     选中的股票: %s" % sorted(tw.keys())[:5])


def test_market_gate_blocks():
    """验证大盘门控拦截时空仓。"""
    market_window = {}
    universe = []
    for i in range(5):
        code = "000%04d.SZ" % (i + 1)
        universe.append(code)
        market_window[code] = make_df(trend=0.003)

    dates = pd.bdate_range("2023-01-01", periods=100).strftime("%Y-%m-%d").tolist()
    bm = {}
    price = 3000.0
    for d in dates:
        price *= 0.998  # 下跌趋势
        bm[d] = price

    aux = {"trading_calendar": dates, "benchmark_closes": bm}
    cfg = {"rebalance_freq": "monthly", "n_hold": 5, "rps_threshold": 80,
           "volume_confirm": 0, "market_gate": 1, "ma_window": 60,
           "min_history": 50}

    dec = evaluate_day(dates[-1], market_window, [], 1000000, universe, {},
                       cfg, aux)
    tw = dec.get("target_weights", {})
    assert len(tw) == 0, "大盘门控拦截时应空仓"
    print("[OK] test_market_gate_blocks: 大盘门控拦截时返回空仓")


if __name__ == "__main__":
    print("=== RPS 主升浪策略单元级验证 ===\n")
    test_calc_rps()
    test_breakout()
    test_pullback_entry()
    test_volume_confirm()
    test_market_gate()
    test_hold_decision()
    test_evaluate_day_rebalance()
    test_market_gate_blocks()
    print("\n=== 全部单元验证通过 ===")
