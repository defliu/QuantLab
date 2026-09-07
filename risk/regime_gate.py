# coding: utf-8
"""Regime gate（低波动环境门控）—— 仅在低波动 regime 持仓，高波动转现。

来源：报告 Top4「arXiv 低波门控 + 波动率缩放」的可迁移内核（**仅门控部分**，
      不含已证伪的稳态 vol 缩放）。与框架既有 market_gate（MA 跌破空仓）同属
      regime 过滤，二者同开易重复 —— 建议二选一（本件用「波动率历史分位」口径，
      market_gate 用「价格 vs MA」口径）。

机制（可直译 Python）：
    r_t = 全市场等权日收益
    hist.append(r_t)
    rv      = std(hist[-lookback:])
    pct     = fraction(hist_window < rv)      # rv 在历史上的低分位比例
    if pct > vol_percentile_threshold:   低波动环境 -> 持仓
    else:                              高波动环境 -> 转现（cash）

fail-open：历史不足 lookback 时返回 False（允许持仓），避免数据缺口误杀。

config 键（默认关）：
    regime_gate:                  0/1
    regime_lookback:             60
    regime_vol_percentile_threshold: 0.50   # 当前 rv 低于历史中位数比例超此值才持仓
"""
import math
from collections import deque


class RegimeGate:
    def __init__(self, lookback=60, vol_percentile_threshold=0.50, enabled=False):
        self.lookback = int(lookback)
        self.vol_percentile_threshold = float(vol_percentile_threshold)
        self.enabled = bool(enabled)
        self._hist = deque(maxlen=max(self.lookback, 500))
        self._last_reason = ""

    def update(self, market_return):
        """推入当日全市场收益，推进状态。返回 bool：今日是否要求空仓。"""
        if market_return is not None:
            self._hist.append(float(market_return))
        if not self.enabled:
            return False
        if len(self._hist) < self.lookback:
            return False  # 数据不足，fail-open
        window = list(self._hist)[-self.lookback:]
        rv = _std(window)
        below = sum(1 for x in window if x < rv)
        pct = below / len(window)
        if pct > self.vol_percentile_threshold:
            self._last_reason = ""
            return False  # 低波动环境 -> 持仓
        self._last_reason = "high_vol_regime pct=%.2f" % pct
        return True  # 高波动环境 -> 转现

    @property
    def reason(self):
        return self._last_reason


def _std(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return math.sqrt(var)
