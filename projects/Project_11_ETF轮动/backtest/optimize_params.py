# -*- coding: utf-8 -*-
"""
参数优化脚本
遍历不同参数组合，找最优
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from itertools import product as iter_product
from datetime import datetime

from simple_etf_rotation.strategy import SimpleETFRotation
from simple_etf_rotation.config import ETF_POOL
from simple_etf_rotation.data_loader import DataLoader


def calc_sharpe_ratio(returns, rf=0.03):
    """计算夏普比率"""
    excess = returns - rf / 252
    if excess.std() == 0:
        return 0
    return np.sqrt(252) * excess.mean() / excess.std()


def run_backtest_with_params(params, etf_data, start_date, end_date):
    """用指定参数跑一次回测"""
    strategy = SimpleETFRotation(etf_pool=ETF_POOL, params=params)

    benchmark = etf_data[params["benchmark"]]
    all_dates = benchmark.index
    all_dates = all_dates[(all_dates >= pd.to_datetime(start_date)) &
                           (all_dates <= pd.to_datetime(end_date))]

    for current_date in all_dates:
        current_etf_data = {}
        current_prices = {}
        for code, df in etf_data.items():
            df_now = df[df.index <= current_date]
            if len(df_now) >= params["slow_ma"]:
                current_etf_data[code] = df_now
                if len(df_now) > 0:
                    current_prices[code] = float(df_now["close"].iloc[-1])

        benchmark_now = benchmark[benchmark.index <= current_date]
        if len(benchmark_now) < params["slow_ma"]:
            continue

        target_pos, regime = strategy.run_strategy(
            current_date=current_date,
            etf_data_dict=current_etf_data,
            benchmark_data=benchmark_now,
            current_prices=current_prices,
        )

        if regime is not None:
            strategy.execute_rebalance(target_pos, current_prices)

    if len(strategy.history) < 2:
        return None

    history_df = pd.DataFrame(strategy.history).set_index("date")
    return history_df


def optimize():
    """参数优化"""
    print("=" * 60)
    print("参数优化")
    print("=" * 60)

    # 1. 加载数据
    loader = DataLoader(data_dir="data")
    codes = list(ETF_POOL.keys())
    if not os.path.exists(f"data/{codes[0]}.csv"):
        print("⚠ 未找到数据，正在生成...")
        loader.generate_sample_data(codes, days=1800)

    etf_data = loader.load_all_etfs(
        codes, "2020-01-01", "2026-08-01"
    )
    print(f"加载 {len(etf_data)} 只ETF数据")

    # 2. 样本内/样本外划分
    train_start = "2020-01-01"
    train_end = "2024-06-30"
    test_start = "2024-07-01"
    test_end = "2026-08-01"

    print(f"\n样本内: {train_start} ~ {train_end}")
    print(f"样本外: {test_start} ~ {test_end}")

    # 3. 参数网格
    param_grid = {
        "fast_ma": [30, 60, 90],
        "slow_ma": [120, 180, 240],
        "momentum_short": [10, 20, 30],
        "momentum_long": [40, 60, 90],
        "top_n": [3, 5],
    }

    # 生成所有参数组合
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combinations = list(iter_product(*values))
    print(f"\n参数组合数: {len(combinations)}")
    print("开始遍历...")

    # 4. 遍历评估
    base_params = {
        "initial_capital": 100000,
        "benchmark": "510300",
        "rebalance_day": 10,
        "commission": 0.0001,
        "stamp_tax": 0.0001,
        "slippage": 0.001,
        "momentum_short_weight": 0.6,
        "momentum_long_weight": 0.4,
        "top_n": 5,  # 默认值，遍历时会覆盖
        # 这些会被param_grid覆盖，但需要存在作为默认
        "fast_ma": 60,
        "slow_ma": 120,
        "momentum_short": 20,
        "momentum_long": 60,
    }

    results = []
    for i, combo in enumerate(combinations):
        params = base_params.copy()
        for k, v in zip(keys, combo):
            # 转换为Python原生类型
            params[k] = int(v) if isinstance(v, (np.integer,)) else float(v) if isinstance(v, (np.floating,)) else v

        # 跑样本内
        try:
            train_history = run_backtest_with_params(
                params, etf_data, train_start, train_end
            )
            if train_history is None or len(train_history) < 100:
                continue

            # 计算样本内指标
            initial = params["initial_capital"]
            final = train_history["total_value"].iloc[-1]
            train_ret = (final / initial - 1) * 100

            cummax = train_history["total_value"].cummax()
            train_dd = (
                (train_history["total_value"] - cummax) / cummax
            ).min() * 100

            # 计算夏普
            train_returns = train_history["total_value"].pct_change().dropna()
            train_sharpe = calc_sharpe_ratio(train_returns)

            results.append(
                {
                    **params,
                    "train_return": train_ret,
                    "train_max_dd": train_dd,
                    "train_sharpe": train_sharpe,
                    "train_score": train_ret / max(abs(train_dd), 1),
                }
            )

            if (i + 1) % 20 == 0:
                print(f"  已完成 {i + 1}/{len(combinations)}")
        except Exception as e:
            print(f"  参数{combo}运行失败: {e}")
            continue

    # 5. 排序找最优
    if not results:
        print("❌ 没有有效结果")
        return

    results_df = pd.DataFrame(results)
    # 用收益回撤比作为评分
    top_results = results_df.nlargest(10, "train_score")

    print("\n" + "=" * 60)
    print("🏆 Top 10 参数组合（按样本内收益回撤比）")
    print("=" * 60)

    for idx, row in top_results.iterrows():
        print(f"\n组合 {idx + 1}:")
        print(f"  fast_ma={row['fast_ma']}, slow_ma={row['slow_ma']}")
        print(f"  momentum_short={row['momentum_short']}, "
              f"momentum_long={row['momentum_long']}, top_n={row['top_n']}")
        print(f"  样本内: 收益={row['train_return']:+.2f}% "
              f"回撤={row['train_max_dd']:.2f}% "
              f"夏普={row['train_sharpe']:.2f}")

    # 6. 最优参数跑样本外
    best_params = top_results.iloc[0].to_dict()
    print(f"\n📋 best_params keys: {list(best_params.keys())}")
    best_params_clean = {}
    for k in base_params.keys():
        v = best_params[k]
        # 转换为Python原生类型
        if isinstance(v, (np.integer,)):
            best_params_clean[k] = int(v)
        elif isinstance(v, (np.floating,)):
            best_params_clean[k] = float(v)
        else:
            best_params_clean[k] = v
    print(f"✅ best_params_clean: {best_params_clean}")

    print("\n" + "=" * 60)
    print("🔬 最优参数样本外测试")
    print("=" * 60)

    test_history = run_backtest_with_params(
        best_params_clean, etf_data, test_start, test_end
    )

    if test_history is not None:
        initial = best_params_clean["initial_capital"]
        final = test_history["total_value"].iloc[-1]
        test_ret = (final / initial - 1) * 100

        cummax = test_history["total_value"].cummax()
        test_dd = (
            (test_history["total_value"] - cummax) / cummax
        ).min() * 100

        print(f"样本外收益: {test_ret:+.2f}%")
        print(f"样本外回撤: {test_dd:.2f}%")
        print(f"样本外夏普: {calc_sharpe_ratio(test_history['total_value'].pct_change().dropna()):.2f}")

        # 保存最优参数
        best_params_clean["train_return"] = best_params["train_return"]
        best_params_clean["test_return"] = test_ret
        best_params_clean["test_max_dd"] = test_dd
        pd.DataFrame([best_params_clean]).to_csv(
            f"results/best_params_{datetime.now():%Y%m%d}.csv", index=False
        )
        print(f"\n✅ 最优参数已保存到 results/best_params_*.csv")

    return top_results


if __name__ == "__main__":
    optimize()