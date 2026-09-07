# coding: utf-8
"""闭环杠杆波动率控制（closed-loop leveraged vol targeting）。

来源：arXiv 2603.01298（Boyd / Candès / Hastie 级别）。报告「可落地量化策略挖掘
2026-08-28」第 1 条。本质是收益风险比放大器——它不改底层信号，只在策略给定
的目标持仓后做统一敞口缩放。前提是底层夏普 > 0.5，否则只是更稳地亏钱。

与现有开环 vol_target（backtest/rebalance.py 第 6 步）的区别：
  开环  w = min(σ^tar/σ̂, L)                 —— 单向，对估计误差敏感、换手爆炸
  闭环  w = min(exp(κ)·σ^tar/σ̂, L)，κ 由跟踪误差反馈驱动，对波动率尖峰更平滑、
         在持续高波动期主动压得更低（更防御），低波动期由 L 封顶不会加杠杆过头。

本模块是**有状态**的：每次 rebalance / 每个交易日调用 next_weight(r_prev) 推进
EWMA 方差并输出当日敞口 w∈[0, L]。引擎侧若要原生集成，用同一个实例贯穿整段回测。
离线实验（套在已有净值曲线上）直接复用本类即可，无需改动回测引擎。
"""
import math


class ClosedLoopVolTarget:
    """逐期推进的闭环波动率控制器。

    Args:
        sigma_target_daily: 日频目标波动（建议 A股 12%~15% 年化 → 0.12/√252）。
        L:                  杠杆上限，A股建议先锁 1.0（不加杠杆）；论文默认 1.5。
        h:                  EWMA 半衰期（交易日），默认 126。
        g:                  控制器增益，越大修正越猛，默认 55。
        theta:              κ 的惯性平滑系数（0~1），默认 0.6。
        kappa_min/max:      κ 截断，防失控，默认 -1 / +1。
        kappa0:             初始 κ。
    """

    def __init__(self, sigma_target_daily, L=1.0, h=126, g=55.0, theta=0.6,
                 kappa_min=-1.0, kappa_max=1.0, kappa0=0.0):
        self.sigma_target = float(sigma_target_daily)
        self.L = float(L)
        self.beta = math.exp(-math.log(2.0) / float(h))
        self.g = float(g)
        self.theta = float(theta)
        self.kappa_min = float(kappa_min)
        self.kappa_max = float(kappa_max)
        self.kappa = float(kappa0)
        self.v = None          # EWMA 方差（日收益平方的递归估计）
        self.w = 1.0           # 上一期实际应用的敞口（供换手统计）
        self._last_sigma_hat = 0.0

    def next_weight(self, r_prev):
        """用上一期已实现收益 r_prev 推进控制器，返回本期敞口 w∈[0, L]。

        调用顺序：在已持有上期收益 r_{t-1} 后、应用本期敞口到 r_t 之前调用。
        首日（无 r_prev）传 None，返回当前 w 且不更新状态。
        """
        if r_prev is None:
            return self.w
        r2 = float(r_prev) * float(r_prev)
        if self.v is None:
            self.v = r2
        else:
            self.v = (1.0 - self.beta) * r2 + self.beta * self.v
        sigma_hat = math.sqrt(self.v) if self.v > 0 else 0.0
        self._last_sigma_hat = sigma_hat
        if sigma_hat <= 1e-12:
            w = self.L
        else:
            e_k = math.log(sigma_hat / self.sigma_target)   # 跟踪误差（对数）
            raw = -self.g * e_k
            raw_c = max(self.kappa_min, min(self.kappa_max, raw))
            self.kappa = (1.0 - self.theta) * raw_c + self.theta * self.kappa
            w = math.exp(self.kappa) * self.sigma_target / sigma_hat
            w = min(w, self.L)
            if w < 0.0:
                w = 0.0
        self.w = w
        return w

    # ---- 开环对照（供实验比较，复用同一 EWMA 方差估计） ----
    def next_weight_openloop(self, r_prev):
        """开环版：w = min(σ^tar/σ̂, L)，无 κ 反馈。"""
        if r_prev is None:
            return self.w
        r2 = float(r_prev) * float(r_prev)
        if self.v is None:
            self.v = r2
        else:
            self.v = (1.0 - self.beta) * r2 + self.beta * self.v
        sigma_hat = math.sqrt(self.v) if self.v > 0 else 0.0
        if sigma_hat <= 1e-12:
            w = self.L
        else:
            w = min(self.sigma_target / sigma_hat, self.L)
            if w < 0.0:
                w = 0.0
        self.w = w
        return w


def overlay_equity(strategy_daily_returns, controller, openloop=False):
    """把控制器离线套在策略日收益序列上，返回缩放后的权益曲线（list[float]）。

    strategy_daily_returns: 策略自身日收益列表（首个元素通常为 0，即建仓前一日）。
    返回 (equity_list, weight_list)，equity_list[0] = 1.0（起始归一化权益）。
    """
    ctrl = controller
    # 重置状态，保证可重复
    ctrl.v = None
    ctrl.kappa = 0.0
    ctrl.w = 1.0
    fn = ctrl.next_weight_openloop if openloop else ctrl.next_weight
    equity = [1.0]
    weights = [1.0]
    prev_r = None
    for r in strategy_daily_returns:
        if prev_r is None:
            prev_r = r
            continue  # 首个收益（建仓前）不应用敞口
        w = fn(prev_r)
        rp = w * r
        equity.append(equity[-1] * (1.0 + rp))
        weights.append(w)
        prev_r = r
    return equity, weights
