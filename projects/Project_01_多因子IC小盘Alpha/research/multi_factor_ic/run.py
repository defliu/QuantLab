# coding=utf-8
"""多因子 IC 测试入口。"""

import sys
import os
# 确保当前目录在 path 中，避免同名 config 冲突
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
# 也加项目根目录
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from research.multi_factor_ic.data_loader import load_universe, build_panel, get_monthly_rebalance_dates
from research.multi_factor_ic.ic_test import run_ic_test, generate_report
from research.multi_factor_ic.config import BASIC_PATH, OUTPUT_DIR


def main():
    print("=" * 50)
    print("多因子选股 IC 测试 v0.1")
    print("=" * 50)

    print("\n[1/6] 加载成分股池...")
    universe = load_universe()
    print(f"      候选池: {len(universe)} 只股票")

    print("\n[2/6] 构建面板数据...")
    panel, fin_ffill = build_panel(universe)
    print(f"      面板尺寸: {panel.shape}")

    print("\n[3/6] 加载基础信息...")
    basic_df = pd.read_parquet(BASIC_PATH)
    print(f"      基础信息: {len(basic_df)} 条")

    print("\n[4/6] 运行 IC 测试...")
    ic_df = run_ic_test(panel, fin_ffill, basic_df)

    print("\n生成因子IC报告...")
    stats_df = generate_report(ic_df)

    print("\n[5/5] 验证综合评分器 IC...")
    from research.multi_factor_ic.scoring import verify_scorer_ic, top_picks
    scorer_ic_df, ic_mean, icir = verify_scorer_ic(panel, fin_ffill)

    latest_date = get_monthly_rebalance_dates(panel)[-1]
    top_picks(panel, fin_ffill, latest_date, n=20)

    print("\n[6/6] 运行组合回测...")
    from research.multi_factor_ic.backtest import run_backtest
    summary_df = run_backtest(panel, fin_ffill, top_n_list=[10, 20, 30])

    print("\n" + "=" * 50)
    print("全部完成!")
    print(f"报告目录: {OUTPUT_DIR}")
    print("=" * 50)


if __name__ == "__main__":
    main()
