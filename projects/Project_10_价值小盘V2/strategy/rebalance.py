# coding=utf-8
"""Project_10 P1-1 增量调仓模块 (2026-08-04)

依据: specs/CodexQT-2026-0803_P1-1_风控组合重构.md 步骤 3
  - signal 模式: 持仓数 < threshold(默认60%) 时触发补仓, 补到 n_target 只
  - 换仓日仍保留双月全量重建作为基线 (mode="full")
  - rebalance_comparison 实测: 方案二(<60%触发) 年化 15.8% > 基线 15.1%,
    换手从 0.91 降到 0.31

用法:
    rb = SignalRebalancer(n_target=80, threshold=0.6)
    if rb.should_replenish(len(holdings)):
        picks = rb.pick(score_series, held=set(holdings), is_banned_fn=..., n_needed=...)
"""


class SignalRebalancer:
    """信号驱动增量补仓器"""

    def __init__(self, n_target=80, threshold=0.6):
        self.n_target = n_target
        self.threshold = threshold

    def should_replenish(self, n_holdings):
        """持仓完整度低于阈值时触发补仓"""
        return n_holdings < self.n_target * self.threshold

    def n_needed(self, n_holdings):
        """需补足的数量"""
        return max(0, self.n_target - n_holdings)

    def pick(self, score, held, is_banned_fn, date=None, n_needed=None):
        """从评分序列中选补仓标的

        Args:
            score: 候选池评分 Series (越高越好)
            held: 当前持仓代码集合
            is_banned_fn: callable(code) -> bool 禁入判断
            date: 评估日期 (可选, 仅供调用方日志/扩展用)
            n_needed: 需要的数量, 默认补满 n_target

        Returns:
            list[code] 按评分从高到低
        """
        if n_needed is None:
            n_needed = self.n_needed(len(held))
        result = []
        for code in score.sort_values(ascending=False).index:
            if len(result) >= n_needed:
                break
            if code in held or is_banned_fn(code):
                continue
            result.append(code)
        return result
