# -*- coding: utf-8 -*-
"""
QMT策略入口文件
放到 D:\QMT_STRATEGIES\simple_etf_rotation\ 下，在QMT中运行
"""
import sys
import os

# 添加策略目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from simple_etf_rotation.strategy import SimpleETFRotation
from simple_etf_rotation.config import ETF_POOL, STRATEGY_PARAMS


# ============ QMT全局变量 ============
strategy = None
current_position = {}
current_cash = STRATEGY_PARAMS["initial_capital"]
current_prices = {}
all_etf_data = {}


def init(ContextInfo):
    """
    QMT策略初始化函数（必需）
    QMT启动时会自动调用此函数
    """
    global strategy, current_position, current_cash, current_prices, all_etf_data

    # 初始化策略实例
    strategy = SimpleETFRotation(etf_pool=ETF_POOL, params=STRATEGY_PARAMS)
    current_position = {}
    current_cash = STRATEGY_PARAMS["initial_capital"]
    current_prices = {}

    # 预加载所有ETF的历史数据
    all_etf_data = {}
    for code in ETF_POOL.keys():
        try:
            data = ContextInfo.get_history(
                count=180,  # 拿180天数据
                field="close",
                stock_code=code,
                period="1d",
            )
            if data is not None and len(data) > 0:
                all_etf_data[code] = data
        except Exception as e:
            print(f"加载 {code} 数据失败: {e}")

    print(f"ETF轮动策略已启动，共加载 {len(all_etf_data)} 只ETF数据")
    print(f"初始资金: {current_cash:,.0f}元")
    print(f"持仓数量: {STRATEGY_PARAMS['top_n']}只")
    print(f"调仓日: 每月{STRATEGY_PARAMS['rebalance_day']}号")


def handle_bar(ContextInfo):
    """
    QMT每根K线触发函数（必需）
    """
    global current_position, current_cash, current_prices

    # 1. 获取当前日期
    bar_datetime = ContextInfo.bar_datetime
    current_date = bar_datetime

    # 2. 获取基准数据（沪深300）
    benchmark_data = all_etf_data.get(STRATEGY_PARAMS["benchmark"])
    if benchmark_data is None:
        return

    # 3. 获取所有ETF的最新数据
    etf_data_dict = {}
    current_prices = {}
    for code in ETF_POOL.keys():
        # 更新最新一天数据
        try:
            latest = ContextInfo.get_market_data_ex(
                ["close"], [code], period="1d", count=1
            )
            if latest is not None and code in latest:
                current_prices[code] = float(latest[code]["close"][-1])
        except Exception as e:
            print(f"获取 {code} 最新价失败: {e}")
            continue

        # 更新历史数据
        if code in all_etf_data:
            etf_data_dict[code] = all_etf_data[code]

    if len(etf_data_dict) < 5:
        print("数据不足，等待数据积累")
        return

    # 4. 调用策略获取目标持仓
    target_position, regime = strategy.run_strategy(
        current_date=current_date,
        etf_data_dict=etf_data_dict,
        benchmark_data=benchmark_data,
        current_prices=current_prices,
    )

    # 5. 如果是调仓日，执行交易
    if regime is not None:
        # 计算实际仓位市值
        position_value = sum(
            shares * current_prices.get(code, 0)
            for code, shares in current_position.items()
        )
        total_value = current_cash + position_value
        current_cash = strategy.cash

        # 执行调仓
        trades, cost = strategy.execute_rebalance(target_position, current_prices)

        # 记录交易日志
        if trades:
            print(
                f"\n[{current_date}] 调仓 | 仓位: {['', '空仓', '满仓/半仓'][int(regime*2)]} | 交易笔数: {len(trades)}"
            )
            for t in trades:
                action = "买入" if t["action"] == "BUY" else "卖出"
                print(
                    f"  {action} {t['code']} {t['shares']}股 @ {t['price']:.3f}元 (手续费:{t['cost']:.2f})"
                )

        # 更新当前持仓
        current_position = strategy.position.copy()
        current_cash = strategy.cash


def handle_tick(ContextInfo):
    """
    Tick数据回调（可选）
    本策略不用逐tick触发，留空
    """
    pass


# ============ 调试用 ============
if __name__ == "__main__":
    """
    本地调试入口
    python qmt_main.py
    """
    print("这是一个QMT策略文件，请在QMT环境中运行")
    print("如需本地回测，请运行: python backtest/local_backtest.py")