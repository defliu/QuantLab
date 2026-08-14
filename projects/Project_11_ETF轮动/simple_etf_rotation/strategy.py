# -*- coding: utf-8 -*-
"""
核心策略类
简化版ETF轮动策略 - MVP1.0
作者：大胖
"""
import pandas as pd
import numpy as np
from datetime import datetime
from .config import STRATEGY_PARAMS, POSITION_RULES, RISK_CONFIG


class SimpleETFRotation:
    """简化版ETF轮动策略"""

    def __init__(self, etf_pool=None, params=None):
        # ETF池
        self.etf_pool = etf_pool
        self.params = params or STRATEGY_PARAMS.copy()

        # 策略参数
        self.top_n = self.params["top_n"]
        self.fast_ma = self.params["fast_ma"]
        self.slow_ma = self.params["slow_ma"]
        self.benchmark = self.params["benchmark"]
        self.mom_short = self.params["momentum_short"]
        self.mom_long = self.params["momentum_long"]
        self.mom_short_w = self.params["momentum_short_weight"]
        self.mom_long_w = self.params["momentum_long_weight"]
        self.rebalance_day = self.params["rebalance_day"]
        self.commission = self.params["commission"]
        self.stamp_tax = self.params["stamp_tax"]
        self.slippage = self.params["slippage"]
        self.initial_capital = self.params["initial_capital"]

        # 运行时状态
        self.position = {}  # 当前持仓 {code: shares}
        self.cash = self.initial_capital  # 现金
        self.total_value = self.initial_capital  # 总市值
        self.history = []  # 净值历史
        self.trades = []  # 交易记录

    # ========================================
    # 1. 择时判断（要不要买？）
    # ========================================
    def get_market_regime(self, benchmark_df):
        """
        均线择时：判断大盘强弱
        返回：仓位比例 0.0 / 0.5 / 1.0
        """
        if benchmark_df is None or len(benchmark_df) < self.slow_ma:
            return POSITION_RULES["empty_position"]

        close = benchmark_df["close"]
        ma60 = close.rolling(self.fast_ma).mean().iloc[-1]
        ma120 = close.rolling(self.slow_ma).mean().iloc[-1]
        current = close.iloc[-1]

        # 均线多头排列 + 站上60日线 → 满仓
        if current > ma60 and ma60 > ma120:
            return POSITION_RULES["full_position"]
        # 站上120日线 → 半仓
        elif current > ma120:
            return POSITION_RULES["half_position"]
        # 跌破120日线 → 空仓
        else:
            return POSITION_RULES["empty_position"]

    # ========================================
    # 2. 计算动量得分
    # ========================================
    def calc_momentum_score(self, etf_df):
        """
        计算ETF动量得分
        得分 = 短期动量 * 0.6 + 长期动量 * 0.4
        """
        if etf_df is None or len(etf_df) < self.mom_long:
            return -999.0

        close = etf_df["close"]

        # 短期动量（20日）
        short_ret = close.iloc[-1] / close.iloc[-self.mom_short] - 1
        # 长期动量（60日）
        long_ret = close.iloc[-1] / close.iloc[-self.mom_long] - 1

        # 综合得分
        return self.mom_short_w * short_ret + self.mom_long_w * long_ret

    # ========================================
    # 3. 选取得分最高的N只ETF
    # ========================================
    def select_top_etfs(self, etf_data_dict, top_n=None):
        """
        选取得分最高的N只ETF
        返回：[(code, score), ...]
        """
        if top_n is None:
            top_n = self.top_n

        scores = {}
        for code in self.etf_pool.keys():
            if code in etf_data_dict and etf_data_dict[code] is not None:
                score = self.calc_momentum_score(etf_data_dict[code])
                scores[code] = score

        # 按得分降序排序
        sorted_etfs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_etfs[:top_n]

    # ========================================
    # 4. 调仓日判断
    # ========================================
    def should_rebalance(self, current_date):
        """是否到调仓日（每月10号）"""
        if isinstance(current_date, str):
            current_date = pd.to_datetime(current_date)
        return current_date.day == self.rebalance_day

    # ========================================
    # 5. 计算当前持仓市值
    # ========================================
    def calc_position_value(self, current_prices):
        """计算当前持仓总市值"""
        position_value = 0
        for code, shares in self.position.items():
            if code in current_prices and current_prices[code] is not None:
                position_value += shares * current_prices[code]
        return position_value

    # ========================================
    # 6. 主策略函数
    # ========================================
    def run_strategy(
        self, current_date, etf_data_dict, benchmark_data, current_prices=None
    ):
        """
        主策略逻辑

        参数:
            current_date: 当前日期
            etf_data_dict: {code: DataFrame} 每只ETF的历史数据
            benchmark_data: DataFrame 基准数据
            current_prices: {code: price} 当前价格（用于调仓计算）

        返回:
            target_position: {code: shares} 目标持仓
            regime: float 仓位比例（用于外部记录）
        """
        # 当前价格默认值
        if current_prices is None:
            current_prices = {
                code: df["close"].iloc[-1]
                for code, df in etf_data_dict.items()
                if df is not None and len(df) > 0
            }

        # 1. 计算当前总市值
        position_value = self.calc_position_value(current_prices)
        self.total_value = self.cash + position_value

        # 2. 记录净值历史
        self.history.append(
            {
                "date": current_date,
                "total_value": self.total_value,
                "cash": self.cash,
                "position_value": position_value,
            }
        )

        # 3. 是否需要调仓
        if not self.should_rebalance(current_date):
            return self.position, None  # 不动

        # 4. 择时判断
        regime = self.get_market_regime(benchmark_data)
        if regime == 0:
            # 清仓信号
            target_position = {}
            self._record_rebalance(current_date, current_prices, [], regime)
            return target_position, regime

        # 5. 选取得分最高的N只ETF
        top_etfs = self.select_top_etfs(etf_data_dict)
        target_codes = [code for code, _ in top_etfs]

        # 6. 计算每只的目标仓位（等权）
        target_capital = self.total_value * regime
        target_value_per_etf = target_capital / self.top_n

        target_position = {}
        for code in target_codes:
            price = current_prices.get(code, 0)
            if price <= 0:
                continue
            # 计算可买入股数（整百股）
            shares = int(target_value_per_etf / price / 100) * 100
            if shares > 0:
                target_position[code] = shares

        # 7. 记录调仓信息
        self._record_rebalance(
            current_date, current_prices, top_etfs, regime
        )

        return target_position, regime

    def _record_rebalance(self, current_date, current_prices, top_etfs, regime):
        """记录调仓日志"""
        log_entry = {
            "date": current_date,
            "regime": regime,
            "position_regime": ["空仓", "半仓", "满仓"][int(regime * 2)],
            "top_etfs": [(code, score, current_prices.get(code, 0))
                          for code, score in top_etfs],
        }
        self.trades.append(log_entry)
        return log_entry

    # ========================================
    # 7. 执行调仓（外部调用接口）
    # ========================================
    def execute_rebalance(self, target_position, current_prices):
        """
        执行调仓
        返回：(trades_list, total_cost)
        trades_list: [{'code', 'action', 'shares', 'price', 'cost'}]
        """
        trades_list = []
        total_cost = 0

        # 1. 卖出不在目标中的
        for code in list(self.position.keys()):
            if code not in target_position:
                price = current_prices.get(code, 0)
                shares = self.position[code]
                if shares > 0 and price > 0:
                    # 卖出成本（佣金+印花税+滑点）
                    cost = shares * price * (self.commission + self.stamp_tax)
                    cost += shares * price * self.slippage
                    self.cash += shares * price - cost
                    total_cost += cost
                    trades_list.append(
                        {
                            "code": code,
                            "action": "SELL",
                            "shares": shares,
                            "price": price,
                            "cost": cost,
                        }
                    )
                    del self.position[code]

        # 2. 买入/调整目标持仓
        for code, target_shares in target_position.items():
            price = current_prices.get(code, 0)
            if price <= 0:
                continue
            current_shares = self.position.get(code, 0)
            diff_shares = target_shares - current_shares

            if diff_shares > 0:
                # 买入
                trade_value = diff_shares * price
                cost = trade_value * self.commission + trade_value * self.slippage
                if self.cash >= trade_value + cost:
                    self.cash -= trade_value + cost
                    self.position[code] = current_shares + diff_shares
                    total_cost += cost
                    trades_list.append(
                        {
                            "code": code,
                            "action": "BUY",
                            "shares": diff_shares,
                            "price": price,
                            "cost": cost,
                        }
                    )
            elif diff_shares < 0:
                # 卖出部分
                sell_shares = abs(diff_shares)
                trade_value = sell_shares * price
                cost = trade_value * (
                    self.commission + self.stamp_tax + self.slippage
                )
                self.cash += trade_value - cost
                self.position[code] = current_shares - sell_shares
                if self.position[code] <= 0:
                    del self.position[code]
                total_cost += cost
                trades_list.append(
                    {
                        "code": code,
                        "action": "SELL",
                        "shares": sell_shares,
                        "price": price,
                        "cost": cost,
                    }
                )

        return trades_list, total_cost

    # ========================================
    # 8. 风险检查
    # ========================================
    def check_risk(self, current_prices):
        """
        风险检查
        返回: (is_risk, reason)
        """
        # 单只ETF止损检查
        for code, shares in list(self.position.items()):
            if code not in current_prices or shares == 0:
                continue
            current_price = current_prices[code]
            # 这里需要成本价，简化版本假设购入价为持仓期间的均价
            # 实际应记录每个持仓的买入价
            pass

        return False, None