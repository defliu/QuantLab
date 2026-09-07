# coding: utf-8
"""ATR_FIX_v4 涨跌停掩码前置对比（T-20260829-001）。

基线 = 框架原生 atr_lowvol（无掩码）；掩码版 = atr_lowvol_mask（mask-aware）。
同参数跑，差值即「涨跌停/停牌虚高」挤掉的水分。

FIX_v4 风格参数：小盘低波，n_hold=8、月频、价格<=50、质量(ROE>0)+动量门控。
窗口 2016-2026（与 OMD/小市值/ETF 一致，便于横向比较）。
"""
import os, sys, json
import numpy as np
import pandas as pd

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from data.astock_reader import AstockParquetReader
from data.mask_reader import MaskParquetReader
from data.universe import load_universe
from backtest.engine import run_backtest

START = "2016-01-04"
END = "2026-08-21"
INIT_CASH = 1_000_000.0
EXEC_CFG = {"price": "next_open", "slippage": 0.001,
            "commission_rate": 0.00025, "tax_rate": 0.0001}
BENCH = "000300.SH"
BENCH_DB = "D:/astock/benchmark/benchmark_index.duckdb"
UNI_CSV = "D:/QuantLab/config/full_a_sh_sz.csv"

import strategy.atr_mask  # noqa: F401 注册 atr_lowvol_mask

ATR_FIX = {
    "rebalance_freq": "monthly", "n_hold": 8, "atr_win": 14,
    "atr_pct_max": 0.06, "turnover_min": 1.0, "turnover_max": 8.0,
    "quality_gate": 1, "momentum_gate": 1, "stop_loss": -0.08,
    "max_price": 50, "position_sizing": "equal", "target_leverage": 1.0,
    "vol_target": 0.0, "industry_cap": 0.0, "max_positions": 0,
    "min_position_value": 0.0,
}


def _run(strategy_name, reader_cls, name):
    uni = load_universe(UNI_CSV)
    reader = reader_cls("D:/astock/daily/stock_daily.parquet", adjustment="hfq")
    try:
        res = run_backtest(
            reader=reader, universe=uni["codes"], start_date=START, end_date=END,
            strategy_config=ATR_FIX, execution_cfg=EXEC_CFG, initial_cash=INIT_CASH,
            benchmark_code=BENCH, benchmark_db_path=BENCH_DB, config_name=name,
            strategy_name=strategy_name, trading_model="next_open",
        )
    finally:
        reader.close()
    return res["summary"]["performance"]


def main():
    print("[run] ATR_FIX_v4 baseline (no mask) ...")
    base = _run("atr_lowvol", AstockParquetReader, "atr_fix_baseline")
    print("[run] ATR_FIX_v4 mask-aware ...")
    mask = _run("atr_lowvol_mask", MaskParquetReader, "atr_fix_mask")

    keys = ["total_return", "cagr", "max_drawdown", "sharpe", "calmar",
            "win_rate", "turnover_annualized", "n_trades", "excess_return",
            "information_ratio"]
    print("\n==== ATR_FIX_v4 掩码前置对比 (窗口 %s~%s) ====" % (START, END))
    print("%-18s" % "指标", *[k[:14].rjust(15) for k in keys])
    for nm, p in (("baseline(无掩码)", base), ("mask-aware", mask)):
        row = [("%.4f" % p.get(k, 0) if isinstance(p.get(k), (int, float)) else str(p.get(k)))
               for k in keys]
        print("%-18s" % nm, *[v.rjust(15) for v in row])
    # 差值（掩码 - 基线）
    print("\n--- 掩码相对基线的差值（挤掉的水分）---")
    for k in ["cagr", "max_drawdown", "sharpe"]:
        d = float(mask.get(k, 0) or 0) - float(base.get(k, 0) or 0)
        print("  %-14s %+.4f" % (k, d))

    out = {"ATR_FIX_baseline": base, "ATR_FIX_mask": mask}
    with open("D:/QuantLab/research_study/atr_mask_compare_result.json", "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n结果已写入 research_study/atr_mask_compare_result.json")


if __name__ == "__main__":
    main()
