# -*- coding: utf-8 -*-
"""
本地回测脚本
不依赖QMT，用本地CSV数据回测
运行: python backtest/local_backtest.py
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt

from simple_etf_rotation.strategy import SimpleETFRotation
from simple_etf_rotation.config import ETF_POOL, STRATEGY_PARAMS
from simple_etf_rotation.data_loader import DataLoader


def run_backtest(start_date="2020-01-01", end_date="2026-08-01",
                 generate_data=True, save_results=True):
    """运行本地回测"""
    print("=" * 60)
    print("ETF轮动策略 - 本地回测")
    print("=" * 60)
    print(f"回测区间: {start_date} ~ {end_date}")
    print(f"初始资金: {STRATEGY_PARAMS['initial_capital']:,.0f}元")
    print(f"持仓数量: {STRATEGY_PARAMS['top_n']}只")
    print(f"调仓日: 每月{STRATEGY_PARAMS['rebalance_day']}号")
    print("=" * 60)

    # 1. 准备数据
    loader = DataLoader(data_dir="data")
    codes = list(ETF_POOL.keys())

    # 如果需要生成模拟数据
    if generate_data and not os.path.exists(f"data/{codes[0]}.csv"):
        print("\n⚠ 未找到历史数据，正在生成模拟数据...")
        loader.generate_sample_data(codes, days=1800)

    # 2. 加载所有ETF数据
    print("\n加载ETF数据...")
    etf_data = loader.load_all_etfs(codes, start_date, end_date)
    print(f"成功加载 {len(etf_data)} 只ETF数据")

    if len(etf_data) < 5:
        print("❌ 数据加载失败，请检查data目录")
        return

    # 3. 初始化策略
    strategy = SimpleETFRotation(etf_pool=ETF_POOL, params=STRATEGY_PARAMS)

    # 4. 准备基准数据
    benchmark_data = etf_data.get(STRATEGY_PARAMS["benchmark"])
    if benchmark_data is None:
        print(f"❌ 基准 {STRATEGY_PARAMS['benchmark']} 数据缺失")
        return

    # 5. 按日期迭代回测
    # 获取所有交易日期
    all_dates = benchmark_data.index
    all_dates = all_dates[(all_dates >= pd.to_datetime(start_date)) &
                           (all_dates <= pd.to_datetime(end_date))]

    print(f"\n开始回测，共 {len(all_dates)} 个交易日...")

    # 6. 主循环
    total_trades = 0
    total_cost = 0

    for i, current_date in enumerate(all_dates):
        # 准备当前数据
        current_etf_data = {}
        current_prices = {}
        for code, df in etf_data.items():
            # 取到当前日期的数据
            df_now = df[df.index <= current_date]
            if len(df_now) >= STRATEGY_PARAMS["slow_ma"]:
                current_etf_data[code] = df_now

        benchmark_now = benchmark_data[benchmark_data.index <= current_date]

        if len(benchmark_now) < STRATEGY_PARAMS["slow_ma"]:
            continue

        # 获取当前价格
        for code, df_now in current_etf_data.items():
            if len(df_now) > 0:
                current_prices[code] = float(df_now["close"].iloc[-1])

        # 调用策略
        target_position, regime = strategy.run_strategy(
            current_date=current_date,
            etf_data_dict=current_etf_data,
            benchmark_data=benchmark_now,
            current_prices=current_prices,
        )

        # 执行调仓
        if regime is not None:
            trades, cost = strategy.execute_rebalance(
                target_position, current_prices
            )
            total_trades += len(trades)
            total_cost += cost

        # 进度提示
        if (i + 1) % 250 == 0:
            current_value = strategy.cash + sum(
                strategy.position.get(code, 0) * current_prices.get(code, 0)
                for code in strategy.position
            )
            ret = (current_value / STRATEGY_PARAMS["initial_capital"] - 1) * 100
            print(f"  {current_date.strftime('%Y-%m-%d')}: "
                  f"净值={current_value:,.0f} 收益={ret:+.2f}%")

    # 7. 计算回测结果
    history_df = pd.DataFrame(strategy.history)
    if len(history_df) == 0:
        print("❌ 回测数据为空")
        return

    history_df.set_index("date", inplace=True)

    # 计算基准收益
    benchmark_ret = (benchmark_data["close"] / benchmark_data["close"].iloc[0]) * \
                    STRATEGY_PARAMS["initial_capital"]
    benchmark_ret = benchmark_ret[
        (benchmark_ret.index >= history_df.index[0]) &
        (benchmark_ret.index <= history_df.index[-1])
    ]

    # 8. 评估指标
    final_value = history_df["total_value"].iloc[-1]
    total_return = (final_value / STRATEGY_PARAMS["initial_capital"] - 1) * 100
    benchmark_return = (benchmark_ret.iloc[-1] / STRATEGY_PARAMS["initial_capital"] - 1) * 100
    excess_return = total_return - benchmark_return

    # 最大回撤
    cummax = history_df["total_value"].cummax()
    drawdown = (history_df["total_value"] - cummax) / cummax
    max_drawdown = drawdown.min() * 100

    # 年化收益
    days = (history_df.index[-1] - history_df.index[0]).days
    annual_return = ((final_value / STRATEGY_PARAMS["initial_capital"]) **
                      (365 / days) - 1) * 100

    # 9. 输出结果
    print("\n" + "=" * 60)
    print("📊 回测结果")
    print("=" * 60)
    print(f"回测区间: {history_df.index[0].strftime('%Y-%m-%d')} ~ "
          f"{history_df.index[-1].strftime('%Y-%m-%d')}")
    print(f"期初资金: {STRATEGY_PARAMS['initial_capital']:,.0f}元")
    print(f"期末净值: {final_value:,.0f}元")
    print(f"累计收益: {total_return:+.2f}%")
    print(f"年化收益: {annual_return:+.2f}%")
    print(f"基准收益: {benchmark_return:+.2f}%")
    print(f"超额收益: {excess_return:+.2f}%")
    print(f"最大回撤: {max_drawdown:.2f}%")
    print(f"调仓次数: {total_trades}笔")
    print(f"累计成本: {total_cost:,.2f}元")
    print("=" * 60)

    # 10. 保存结果
    if save_results:
        history_df.to_csv(f"results/equity_curve_{datetime.now():%Y%m%d}.csv")
        trades_df = pd.DataFrame([
            {
                "date": t["date"],
                "regime": t["position_regime"],
                "code": etf[0],
                "score": etf[1],
                "price": etf[2],
            }
            for t in strategy.trades
            for etf in t["top_etfs"]
        ])
        if len(trades_df) > 0:
            trades_df.to_csv(
                f"results/trades_{datetime.now():%Y%m%d}.csv", index=False
            )
        print(f"\n✅ 结果已保存到 results/ 目录")

        # 11. 画图
        try:
            plt.figure(figsize=(14, 7))
            plt.plot(history_df.index, history_df["total_value"],
                     label="策略净值", linewidth=2, color="red")
            plt.plot(benchmark_ret.index, benchmark_ret.values,
                     label="沪深300ETF基准", linewidth=1.5,
                     color="blue", alpha=0.7)
            plt.fill_between(history_df.index,
                            history_df["total_value"].cummax(),
                            history_df["total_value"],
                            alpha=0.3, color="red",
                            label=f"回撤区 (最大{max_drawdown:.1f}%)")
            plt.title("ETF轮动策略 vs 沪深300ETF基准", fontsize=14)
            plt.xlabel("日期")
            plt.ylabel("净值(元)")
            plt.legend(loc="upper left")
            plt.grid(True, alpha=0.3)
            plt.tight_layout()

            chart_path = f"results/backtest_{datetime.now():%Y%m%d}.png"
            plt.savefig(chart_path, dpi=100, bbox_inches="tight")
            print(f"📊 图表已保存: {chart_path}")
            plt.close()
        except Exception as e:
            print(f"图表生成失败: {e}")

    return {
        "final_value": final_value,
        "total_return": total_return,
        "annual_return": annual_return,
        "benchmark_return": benchmark_return,
        "excess_return": excess_return,
        "max_drawdown": max_drawdown,
        "total_trades": total_trades,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ETF轮动策略本地回测")
    parser.add_argument("--start", default="2020-01-01", help="开始日期")
    parser.add_argument("--end", default="2026-08-01", help="结束日期")
    parser.add_argument("--no-gen", action="store_true", help="不生成模拟数据")
    args = parser.parse_args()

    run_backtest(
        start_date=args.start,
        end_date=args.end,
        generate_data=not args.no_gen,
    )