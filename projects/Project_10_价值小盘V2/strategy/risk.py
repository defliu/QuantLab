# coding=utf-8
"""V2 风控模块：止损/回撤/持有期/换手限制"""
import json
import os
from datetime import datetime, timedelta
import pandas as pd


class RiskController:
    """个股止损 + 组合回撤 + 持有期 + 换手限制"""

    def __init__(self, stop_loss=0.08, max_drawdown=0.15,
                 max_holding_days=60, max_daily_turnover=0.30,
                 state_file=None):
        self.stop_loss = stop_loss
        self.max_drawdown = max_drawdown
        self.max_holding_days = max_holding_days
        self.max_daily_turnover = max_daily_turnover
        self.state_file = state_file
        self._state = self._load_state()

    def _load_state(self):
        default = {
            "holdings": {},       # code -> {"entry_date": str, "entry_price": float}
            "nav_peak": 1.0,
            "禁入列表": {},       # code -> until_date (止损后禁入)
        }
        if self.state_file and os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return default

    def save_state(self):
        if self.state_file:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)

    def update_holdings(self, current_holdings, current_prices, today):
        """更新持仓状态，返回需要卖出的列表"""
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

            # 2. 个股止损
            pnl = current_price / entry_price - 1.0
            if pnl <= -self.stop_loss:
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
        """组合回撤检查，返回是否触发清仓"""
        if current_nav > self._state["nav_peak"]:
            self._state["nav_peak"] = current_nav
        drawdown = 1.0 - current_nav / self._state["nav_peak"]
        if drawdown >= self.max_drawdown:
            self._state["nav_peak"] = current_nav
            return True, drawdown
        return False, drawdown

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
