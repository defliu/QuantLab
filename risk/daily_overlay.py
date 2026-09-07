# coding: utf-8
"""Daily risk overlay —— 把 CrashGuard + RegimeGate 串成引擎每日钩子。

接入点（backtest/engine.py 主循环）：
    在 strategy decision 生成后、成为 pending 前，调用
        decision = risk_overlay.apply(decision, today, pf, window, strategy_config, industry_map)
    若任一守卫要求空仓，则用框架既有 target_weights_to_decision({}) 路径
    产出 full-exit decision（覆盖策略原意，强制转现）。

设计要点：
  * 有状态（guard/gate 内部 deque）单实例贯穿整段回测，独立于调仓频率。
  * 仅在 crash_guard / regime_gate 开关打开时生效，默认双关 -> 对现有回测零影响。
  * 覆写走「空 target_weights -> full exit」，复用框架既有拒单/涨跌停逻辑，
    不重复实现卖出簿记。

config 键：
    crash_guard / crash_lookback / crash_threshold / crash_cooldown
    regime_gate / regime_lookback / regime_vol_percentile_threshold
"""
from collections import deque

from risk.crash_guard import CrashGuard
from risk.regime_gate import RegimeGate


def build_daily_risk_overlay(cfg):
    """从 strategy_config 构造 DailyRiskOverlay（config 驱动）。"""
    cfg = cfg or {}
    guard = CrashGuard(
        crash_lookback=cfg.get("crash_lookback", 5),
        crash_threshold=cfg.get("crash_threshold", -0.07),
        crash_cooldown=cfg.get("crash_cooldown", 5),
        enabled=bool(cfg.get("crash_guard", 0)),
    )
    gate = RegimeGate(
        lookback=cfg.get("regime_lookback", 60),
        vol_percentile_threshold=cfg.get("regime_vol_percentile_threshold", 0.50),
        enabled=bool(cfg.get("regime_gate", 0)),
    )
    return DailyRiskOverlay(guard, gate)


def _equal_weight_market_return(window):
    """用 window（{code: df<=today}）算今日全市场等权日收益（截面均值）。
    任一标的需 >=2 根 bar 才算收益；无足够数据返回 None。"""
    rets = []
    for df in (window or {}).values():
        if df is None or len(df) < 2:
            continue
        close = df["close"].astype(float).values
        if close[-1] <= 0 or close[-2] <= 0:
            continue
        rets.append(close[-1] / close[-2] - 1.0)
    if not rets:
        return None
    return sum(rets) / len(rets)


class DailyRiskOverlay:
    def __init__(self, guard, gate):
        self.guard = guard
        self.gate = gate

    @property
    def enabled(self):
        return self.guard.enabled or self.gate.enabled

    def apply(self, decision, today, pf, window, strategy_config, industry_map):
        """返回（可能覆写为 full-exit 的）decision。"""
        if not self.enabled:
            return decision
        mr = _equal_weight_market_return(window)
        cash_guard = self.guard.update(mr)
        cash_gate = self.gate.update(mr)
        if not (cash_guard or cash_gate):
            return decision
        reason = self.guard.reason or self.gate.reason
        # 强制空仓：复用框架 full-exit 路径（带涨跌停/停牌拒单）
        from backtest.rebalance import target_weights_to_decision
        new_dec = target_weights_to_decision(
            {}, pf, today, strategy_config, window, industry_map)
        logs = list(new_dec.get("logs", []))
        logs.append("risk_overlay: forced cash (%s) @%s" % (reason, today))
        new_dec["logs"] = logs
        diag = dict(new_dec.get("diagnostics", {}))
        diag["risk_overlay_exit"] = reason
        new_dec["diagnostics"] = diag
        return new_dec
