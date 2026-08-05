# coding=utf-8
"""V2 风控模块：止损/回撤/持有期/换手限制

P1-1 (2026-08-04): 新增 ATR 自适应止损 + 分层降仓:
  - atr_stop=True 时, 止损阈值 = min(multiplier * ATR%, stop_cap), 替代固定 8%
  - check_drawdown_tiered(): 10%→降70%仓, 15%→降40%仓, 20%→清仓 (带状态防重触发)
  - check_drawdown() 保留兼容 (旧一刀切口径)
"""
import json
import os
from datetime import datetime, timedelta
import pandas as pd


class RiskController:
    """个股止损 + 组合回撤 + 持有期 + 换手限制"""

    def __init__(self, stop_loss=0.08, max_drawdown=0.15,
                 max_holding_days=60, max_daily_turnover=0.30,
                 state_file=None, atr_stop=False, atr_multiplier=2.0,
                 atr_stop_cap=0.10, tiered_drawdown=False,
                 tiered_thresholds=(0.10, 0.15, 0.20),
                 tiered_targets=(0.70, 0.40, 0.0)):
        self.stop_loss = stop_loss
        self.max_drawdown = max_drawdown
        self.max_holding_days = max_holding_days
        self.max_daily_turnover = max_daily_turnover
        self.state_file = state_file
        # P1-1: ATR 自适应止损
        self.atr_stop = atr_stop
        self.atr_multiplier = atr_multiplier
        self.atr_stop_cap = atr_stop_cap
        # P1-1: 分层降仓
        self.tiered_drawdown = tiered_drawdown
        self.tiered_thresholds = tuple(tiered_thresholds)
        self.tiered_targets = tuple(tiered_targets)
        self._state = self._load_state()

    def _load_state(self):
        default = {
            "holdings": {},       # code -> {"entry_date": str, "entry_price": float}
            "nav_peak": 1.0,
            "禁入列表": {},       # code -> until_date (止损后禁入)
            "dd_tier": 0,         # P1-1: 已触发的回撤分层 (0=未触发)
        }
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                st.setdefault("dd_tier", 0)
                return st
            except Exception:
                pass
        return default

    def save_state(self):
        if self.state_file:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)

    def stop_threshold(self, code=None, atr_pct=None):
        """个股止损阈值 (正数, 亏损超过则卖)

        atr_stop 开启且提供 atr_pct 时: min(multiplier * ATR%, stop_cap)
        否则回退固定 stop_loss。保底不超过 stop_cap (默认 -10%)。
        """
        if self.atr_stop and atr_pct is not None:
            a = atr_pct.get(code) if isinstance(atr_pct, dict) else atr_pct
            if a is not None and a > 0:
                return min(self.atr_multiplier * float(a), self.atr_stop_cap)
        return self.stop_loss

    def update_holdings(self, current_holdings, current_prices, today, atr_pct=None):
        """更新持仓状态，返回需要卖出的列表

        Args:
            atr_pct: P1-1 可选, code -> ATR(14)/close 比值 (atr_stop 时生效)
        """
        today = pd.Timestamp(today) if not isinstance(today, pd.Timestamp) else today
        sells = []

        # 1. 同步全量持仓纳管
        for code in list(self._state["holdings"].keys()):
            if code not in current_holdings:
                del self._state["holdings"][code]

        for code, info in list(self._state["holdings"].items()):
            entry_price = info["entry_price"]
            entry_date = pd.Timestamp(info["entry_date"])
            current_price = current_prices.get(code)

            if current_price is None or current_price <= 0:
                continue

            # 2. 个股止损 (固定 8% 或 ATR 自适应)
            pnl = current_price / entry_price - 1.0
            if pnl <= -self.stop_threshold(code, atr_pct):
                sells.append(code)
                self._state["禁入列表"][code] = (today + timedelta(days=30)).strftime("%Y-%m-%d")
                del self._state["holdings"][code]
                continue

            # 3. 最长持有期
            holding_days = (today - entry_date).days
            if holding_days >= self.max_holding_days:
                sells.append(code)
                del self._state["holdings"][code]

        return sells

    def check_drawdown(self, current_nav):
        """组合回撤检查，返回是否触发清仓

        .. deprecated:: P1-1 后推荐用 check_drawdown_tiered(), 本方法保留旧口径兼容
        """
        if current_nav > self._state["nav_peak"]:
            self._state["nav_peak"] = current_nav
        drawdown = 1.0 - current_nav / self._state["nav_peak"]
        if drawdown >= self.max_drawdown:
            self._state["nav_peak"] = current_nav
            return True, drawdown
        return False, drawdown

    def check_drawdown_tiered(self, current_nav):
        """P1-1 分层回撤检查

        Returns:
            (action, drawdown):
              action ∈ {"none", "reduce", "clear"}
              reduce 时 self.last_tier_target 为目标仓位比例 (如 0.70/0.40)
        分层: >=tier1 降到 targets[0]; >=tier2 降到 targets[1]; >=tier3 清仓。
        带状态防重触发: 仅在回撤加深到新层级时返回动作; 创净值新高时重置层级。
        """
        self.last_tier_target = None
        if current_nav > self._state["nav_peak"]:
            self._state["nav_peak"] = current_nav
            self._state["dd_tier"] = 0
        drawdown = 1.0 - current_nav / self._state["nav_peak"]
        if drawdown <= 0:
            self._state["dd_tier"] = 0
            return "none", drawdown
        tier = 0
        for i, th in enumerate(self.tiered_thresholds):
            if drawdown >= th:
                tier = i + 1
        if tier == 0:
            return "none", drawdown
        if tier <= self._state["dd_tier"]:
            return "none", drawdown  # 已触发过该层级, 不重复动作
        self._state["dd_tier"] = tier
        if tier >= len(self.tiered_thresholds) or self.tiered_targets[tier - 1] <= 0:
            self._state["nav_peak"] = current_nav
            self._state["dd_tier"] = 0
            return "clear", drawdown
        self.last_tier_target = self.tiered_targets[tier - 1]
        return "reduce", drawdown

    def is_banned(self, code, today):
        """检查股票是否在禁入期"""
        until = self._state["禁入列表"].get(code)
        if until is None:
            return False
        return pd.Timestamp(today) <= pd.Timestamp(until)

    def register_entry(self, code, price, today):
        """登记买入"""
        self._state["holdings"][code] = {
            "entry_date": pd.Timestamp(today).strftime("%Y-%m-%d"),
            "entry_price": float(price),
        }

    def register_exit(self, code):
        """登记卖出"""
        self._state["holdings"].pop(code, None)

    def check_turnover(self, n_sells, n_buys, total_position):
        """换手率限制，返回实际可操作数量"""
        if total_position == 0:
            return n_sells, n_buys
        turnover = (n_sells + n_buys) / total_position
        if turnover > self.max_daily_turnover:
            scale = self.max_daily_turnover / turnover
            n_sells = int(n_sells * scale)
            n_buys = int(n_buys * scale)
        return n_sells, n_buys

    @property
    def holdings(self):
        return self._state["holdings"]

    @property
    def nav_peak(self):
        return self._state["nav_peak"]
