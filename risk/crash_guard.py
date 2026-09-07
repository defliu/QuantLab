# coding: utf-8
"""Crash-empty guard（崩盘空仓守卫）—— 持仓期内每日尾部风险 overlay。

来源：报告 Top1「中泰·RV目标控仓+崩溃空仓」中被 vol_target 叠加之外的那一半：
      纯尾部保护（市场急跌转现 + 冷却），**不碰稳态波动率缩放**，因此不会踩
      vol_target 在 ATR_FIX_v4 上「ATR 波动 ~18.5% > 目标 15% -> 永久低仓」的坑
      （见 projects/Project_ATR_lowvol/results/闭环波动率控制_离线实验_20260829.md）。

为什么它可能增量有效而 VT 不行：
      VT 是「持续把波动压到目标」，ATR 自身波动已高于目标 -> 匀速砍仓、削复利主力年；
      本守卫只在「崩盘信号」出现后短暂转现 + 冷却，正常年份完全不动 -> 专治尾部回撤，
      预期压低 -24.8% 最大回撤而不显著伤年化。

机制（可直译 Python）：
    r_t = 全市场等权日收益（截面均值）
    hist.append(r_t)
    if sum(hist[-crash_lookback:]) <= crash_threshold and cooldown==0:
        cooldown = crash_cooldown
    if cooldown > 0:
        cooldown -= 1
        return True          # 强制空仓
    return False

有状态：run_backtest 内单实例贯穿整段回测，每日推入一次 r_t。

config 键（默认关）：
    crash_guard:       0/1
    crash_lookback:    5      # 崩盘判定回看交易日
    crash_threshold:   -0.07  # 该窗口累计收益 <= 此值触发
    crash_cooldown:    5      # 触发后强制空仓交易日
"""
from collections import deque


class CrashGuard:
    def __init__(self, crash_lookback=5, crash_threshold=-0.07,
                 crash_cooldown=5, enabled=False):
        self.crash_lookback = int(crash_lookback)
        self.crash_threshold = float(crash_threshold)
        self.crash_cooldown = int(crash_cooldown)
        self.enabled = bool(enabled)
        self._hist = deque(maxlen=max(self.crash_lookback, 250))
        self._cooldown = 0
        self._last_reason = ""

    def update(self, market_return):
        """推入当日全市场收益，推进状态。返回 bool：今日是否要求空仓。"""
        if market_return is not None:
            self._hist.append(float(market_return))
        if not self.enabled:
            return False
        # 触发检测（仅在非冷却期）
        if self._cooldown == 0 and len(self._hist) >= self.crash_lookback:
            recent = sum(list(self._hist)[-self.crash_lookback:])
            if recent <= self.crash_threshold:
                self._cooldown = self.crash_cooldown
                self._last_reason = "crash:%.1f%%<%.1f%%" % (
                    recent * 100.0, self.crash_threshold * 100.0)
        if self._cooldown > 0:
            self._cooldown -= 1
            if not self._last_reason:
                self._last_reason = "crash_cooldown"
            return True
        self._last_reason = ""
        return False

    @property
    def reason(self):
        return self._last_reason
