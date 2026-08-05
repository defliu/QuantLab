# coding=utf-8
"""大盘择时增强测试入口。"""
import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path: sys.path.insert(0, _THIS_DIR)
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path: sys.path.insert(0, _PROJ_ROOT)

import pandas as pd
import warnings
warnings.filterwarnings("ignore")

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.config import BASIC_PATH
from research.multi_factor_ic.timing import compare_timing_params


def main():
    universe = load_universe()
    panel, fin_ffill = build_panel(universe)
    compare_timing_params(panel, fin_ffill)

if __name__ == "__main__":
    main()
