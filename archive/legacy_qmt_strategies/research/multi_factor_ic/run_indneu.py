# coding=utf-8
"""行业中性化对比入口。"""
import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path: sys.path.insert(0, _THIS_DIR)
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path: sys.path.insert(0, _PROJ_ROOT)

import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.backtest import compare_industry_neutralize
from research.multi_factor_ic.config import BASIC_PATH


def main():
    universe = load_universe()
    panel, fin_ffill = build_panel(universe)
    basic_df = pd.read_parquet(BASIC_PATH)
    compare_industry_neutralize(panel, fin_ffill, basic_df)


if __name__ == "__main__":
    main()
