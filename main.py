# coding=utf-8
"""QuantLab 主入口 — 回测模式"""

import os
import sys
import yaml
import pandas as pd
import numpy as np
from datetime import datetime

# 添加项目根目录到 path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# 添加 Project_01 目录，使 research/（vendored multi_factor_ic 包）可导入
_P01 = os.path.join(PROJECT_ROOT, "projects", "Project_01_多因子IC小盘Alpha")
if _P01 not in sys.path:
    sys.path.insert(0, _P01)


def load_config():
    """加载配置"""
    config_path = os.path.join(PROJECT_ROOT, 'config', 'settings.yaml')
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def run_backtest(strategy_name="project_01"):
    """运行回测"""
    print("=" * 60)
    print("QuantLab 回测系统")
    print("=" * 60)

    config = load_config()
    print("配置加载完成")

    if strategy_name == "project_01":
        # 使用 Project_01 的新模块
        from projects.Project_01_多因子IC小盘Alpha.strategy.scoring import score, FACTOR_WEIGHTS
        from research.multi_factor_ic.data_loader import build_panel, load_universe
        from research.multi_factor_ic.backtest import backtest

        # 加载配置
        p01_cfg = config.get("project_01", {})

        print("加载数据...")
        codes = load_universe()
        panel, fin_ffill = build_panel(codes)

        # 小盘过滤
        def smallcap_filter(panel, fin_ffill, date):
            date_data = panel.loc[date]
            mv = date_data["circ_mv"]
            return (mv > 0) & (mv < 300000)

        bt_cfg = p01_cfg.get("backtest", {})
        print("运行回测 [%s ~ %s]..." % (bt_cfg.get("start_date", "2018-07-01"),
                                         bt_cfg.get("end_date", "2026-06-30")))
        equity_df, trades_df, metrics = backtest(
            panel, fin_ffill,
            top_n=bt_cfg.get("top_n", 80),
            freq=bt_cfg.get("rebalance_freq", "2M"),
            tx_cost=bt_cfg.get("tx_cost", 0.002),
            dynamic_universe=False,  # 小盘过滤用filter_func，不用dynamic_universe
            filter_func=smallcap_filter,
            weights=FACTOR_WEIGHTS,
        )

        print("\n回测结果 (Project_01 + VWAP):")
        for k, v in metrics.items():
            print("  %s: %s" % (k, v))

        return equity_df, trades_df, metrics

    else:
        # 兼容旧版
        from research.multi_factor_ic.backtest import backtest
        from research.multi_factor_ic.data_loader import build_panel, load_universe
        from research.multi_factor_ic.config import START_DATE, END_DATE

        print("加载数据...")
        codes = load_universe()
        panel, fin_ffill = build_panel(codes)

        print("运行回测 [%s ~ %s]..." % (START_DATE, END_DATE))
        equity_df, trades_df, metrics = backtest(panel, fin_ffill, top_n=80, freq="2M")

        print("\n回测结果:")
        for k, v in metrics.items():
            print("  %s: %s" % (k, v))

        return equity_df, trades_df, metrics


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="QuantLab 回测系统")
    parser.add_argument("--strategy", default="project_01", help="策略名称")
    parser.add_argument("--start", help="开始日期")
    parser.add_argument("--end", help="结束日期")
    args = parser.parse_args()

    run_backtest(args.strategy)
