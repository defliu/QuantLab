# coding: utf-8
"""ATR 低波动策略 —— QuantLab 框架原生版（target_weights 模式）。

把 atr_lowvol/backtest_atr_lowvol_v3.py 的逻辑用框架通用层重写：
  * 选股：ATR% 最低分位 + 换手率[1,8]% + 非ST + 上市≥60日 + 质量(ROE>0) + 动量门控(12-1月>0)
  * 调仓：月频/季频（config rebalance_freq）
  * 仓位/风险：全交给框架组合层（equal/vol_parity、vol_target、industry_cap、
               target_leverage 两融、max_positions、min_position_value）
  * 真实约束：框架 execution 自带涨跌停/停牌/滑点/整数手/ST±5%

策略本身只负责"选股 + 返回等权意愿"，组合层负责一切订单簿记与风控。
这正证明：任何策略只要输出 target_weights，就能即插即跑、且自动带真实约束。

config（strategy_params）示例：
  rebalance_freq: quarterly
  n_hold: 50
  atr_win: 14
  atr_pct_max: 0.06
  turnover_min: 1.0
  turnover_max: 8.0
  quality_gate: 1          # ROE>0
  momentum_gate: 1         # 12-1月动量>0（剔除近期输家）
  stop_loss: -0.08
  position_sizing: vol_parity
  target_leverage: 1.5     # 两融（需账户支持）
  vol_target: 0.10
  industry_cap: 0.15
"""
from strategy.registry import register_strategy
from strategy.schedule import is_rebalance_day
from factors.atr import atr_pct
from factors.roe import get_roe_asof

ALLOWED_TRADING_MODELS = ["next_open"]


def _hold_decision(current_date, positions, stop_loss):
    """非调仓日：保持持仓；若触发止损则退出对应标的（一次性再平衡）。"""
    if stop_loss is None or stop_loss >= 0:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }
    stopped = []
    keep = {}
    for p in positions:
        cost = float(p.get("cost_price", 0)) or 0.0
        last = float(p.get("last_price", 0)) or 0.0
        if cost > 0:
            pnl = (last - cost) / cost
        else:
            pnl = 0.0
        if pnl <= stop_loss:
            stopped.append(p["code"])
        else:
            keep[p["code"]] = 1.0
    if not stopped:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["hold"], "candidate_total": 0,
                            "candidate_passed": 0},
            "logs": ["%s hold" % current_date],
        }
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": keep,  # 退出 stopped，其余保持
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {"warnings": ["stop_loss_exit:%d" % len(stopped)],
                        "candidate_total": 0, "candidate_passed": len(keep)},
        "logs": ["%s stop_loss exit %s" % (current_date, stopped)],
    }


@register_strategy("atr_lowvol")
def evaluate_day(current_date, market_window, positions, cash, universe,
                 account_state, strategy_config, aux_data):
    cfg = strategy_config or {}
    freq = cfg.get("rebalance_freq", "monthly")
    n_hold = int(cfg.get("n_hold", 100))
    atr_win = int(cfg.get("atr_win", 14))
    atr_pct_max = float(cfg.get("atr_pct_max", 0.06))
    turnover_min = float(cfg.get("turnover_min", 1.0))
    turnover_max = float(cfg.get("turnover_max", 8.0))
    quality_gate = int(cfg.get("quality_gate", 1))
    momentum_gate = int(cfg.get("momentum_gate", 1))
    stop_loss = cfg.get("stop_loss", None)
    if stop_loss is not None:
        stop_loss = float(stop_loss)
    min_history = int(cfg.get("min_history", 252))

    if not is_rebalance_day(current_date, freq,
                            (aux_data or {}).get("trading_calendar")):
        return _hold_decision(current_date, positions, stop_loss)

    valid = [c for c in universe
             if c in market_window and len(market_window[c]) >= min_history]
    if not valid:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_valid_universe"],
                            "candidate_total": 0, "candidate_passed": 0},
            "logs": ["%s no valid universe" % current_date],
        }

    eligible = []
    for c in valid:
        df = market_window[c]
        last = df.iloc[-1]
        # 换手率过滤
        to = last.get("turnover_rate")
        if to is None or not (turnover_min <= float(to) <= turnover_max):
            continue
        # 非 ST
        if bool(last.get("is_st", False)):
            continue
        # ATR% 过滤（低波动）
        ap = atr_pct(df, atr_win)
        if ap <= 0 or ap > atr_pct_max:
            continue
        # 质量门控 ROE>0
        if quality_gate:
            roe = get_roe_asof(c, current_date)
            if roe is None or roe <= 0:
                continue
        # 动量门控：12-1 月收益 > 0（跳过最近 1 月）
        if momentum_gate:
            close = df["close"].astype(float).values
            if len(close) >= 252:
                ret_12_1 = close[-21] / close[-252] - 1.0 if close[-252] > 0 else 0.0
                if ret_12_1 <= 0:
                    continue
        eligible.append((c, ap))

    # 按 ATR% 升序取前 n_hold（低波动优先）
    eligible.sort(key=lambda x: x[1])
    selected = [c for c, _ in eligible[:n_hold]]

    if not selected:
        return {
            "sell_decisions": [], "buy_candidates": [],
            "target_positions": [], "blocked_candidates": [],
            "diagnostics": {"warnings": ["no_selection"],
                            "candidate_total": len(valid), "candidate_passed": 0},
            "logs": ["%s no selection from %d" % (current_date, len(valid))],
        }

    target_weights = {c: 1.0 for c in selected}
    return {
        "sell_decisions": [], "buy_candidates": [],
        "target_weights": target_weights,
        "target_positions": [], "blocked_candidates": [],
        "diagnostics": {
            "warnings": [],
            "candidate_total": len(valid),
            "candidate_passed": len(selected),
        },
        "logs": ["%s rebalance: %d selected (ATR%%<=%.3f) from %d"
                 % (current_date, len(selected), atr_pct_max, len(valid))],
    }
