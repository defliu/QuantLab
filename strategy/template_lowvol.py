# coding: utf-8
"""TEMPLATE — 即插即跑的目标权重策略（target_weights 模式）。

这是"任何策略拿过来都能跑"的最小范例。你只需要做两件事：
  1. 在调仓日挑出想持有的股票集合；
  2. 返回 target_weights = {code: 1.0 for code in 选中}（等权意愿）。

其余全部交给框架（backtest/rebalance.py 的通用组合层）：
  * 自动算买卖差（卖出不在目标里的持仓、买入新增、加减仓）
  * 仓位模型：equal / vol_parity / custom   （config: position_sizing）
  * 行业上限：industry_cap                   （config: industry_cap）
  * 波动率目标：vol_target                   （config: vol_target）
  * 杠杆上限：target_leverage（两融，自动计提利息） （config: target_leverage）
  * 最大持仓数 / 最小持仓金额                （config: max_positions / min_position_value）
  * 真实约束：涨停买不进 / 跌停卖不出 / 停牌拒单 / 滑点 / 整数手 / ST±5% 涨跌停

配置项（在 yaml 的 strategy_params 或 runner 传入）：
  rebalance_freq: monthly        # weekly|monthly|quarterly
  n_hold: 30                      # 选股数量
  vol_window: 60                  # 波动率估计窗口
  position_sizing: equal          # equal|vol_parity|custom
  target_leverage: 1.0            # >1 启用两融
  vol_target: 0.0                 # >0 启用波动率目标
  industry_cap: 0.0               # >0 启用行业上限
本模板用"最近 vol_window 日波动率最低的前 n_hold 只"做演示信号。
"""
from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day
from factors.volatility import ann_vol

ALLOWED_TRADING_MODELS = ["next_open"]


def _empty_decision(current_date, reason):
    return {
        "sell_decisions": [],
        "buy_candidates": [],
        "target_positions": [],
        "blocked_candidates": [],
        "diagnostics": {"warnings": [reason], "candidate_total": 0,
                        "candidate_passed": 0},
        "logs": ["evaluate_day %s skip: %s" % (current_date, reason)],
    }


@register_strategy("template_lowvol")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "monthly")
    n_hold = int(cfg.get("n_hold", 30))
    vol_window = int(cfg.get("vol_window", 60))
    min_history = int(cfg.get("min_history", vol_window + 5))

    # 非调仓日：保持现状（返回空 target_weights = 不操作）
    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        return _empty_decision(current_date, "non_rebalance_day")

    # 1) 预筛：有足够历史
    valid = [c for c in universe
             if c in market_window and len(market_window[c]) >= min_history]
    if not valid:
        return _empty_decision(current_date, "no_valid_universe")

    # 2) 信号：选 vol_window 日波动率最低的前 n_hold 只
    vols = {}
    for c in valid:
        v = ann_vol(market_window[c], vol_window)
        if v > 0:
            vols[c] = v
    ranked = sorted(vols.items(), key=lambda kv: kv[1])[:n_hold]
    selected = [c for c, _ in ranked]

    if not selected:
        return _empty_decision(current_date, "no_selection")

    # 3) 返回等权意愿；具体权重/杠杆/行业cap 由框架组合层按 config 施加
    target_weights = {c: 1.0 for c in selected}
    return {
        "sell_decisions": [],
        "buy_candidates": [],
        "target_weights": target_weights,
        "target_positions": [],
        "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(valid),
            "candidate_passed": len(selected),
        },
        "logs": ["%s rebalance: selected %d lowest-vol from %d"
                 % (current_date, len(selected), len(valid))],
    }
