# coding: utf-8
"""备用数据源 (E:/huicexitong/gpsj.duckdb) vs 主数据源 (E:/astock) 同区间 ATR MAX5 回测对比.

口径已对齐验证: gpsj 取 '不复权_*' 列 + '复权因子'，与 astock raw 口径逐字段一致。
区间: 2025 全年 (两源均有数据; gpsj 数据最新 2026-04-03，不越界)。
用法: python research_audit/compare_gpsj_astock_2025.py
"""
import io
import logging
import os
import sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.hashing import compute_config_hash, compute_universe_hash
from backtest.engine import run_backtest
from data.astock_reader import AstockParquetReader
from data.gpsj_reader import GpsjDuckDBReader
from data.universe import load_universe

CONFIG = "D:/QuantLab/projects/Project_ATR_lowvol/config/atr_10w_price50_a_max.yaml"
START = "2025-01-01"
END = "2025-12-31"


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = yaml.safe_load(open(CONFIG, encoding="utf-8"))
    bt = dict(cfg["backtest"])
    bt["start_date"] = START
    bt["end_date"] = END
    strat_cfg = cfg["strategy_params"]
    exec_cfg = cfg["execution"]
    uni = load_universe(cfg["universe"]["csv"])
    universe = uni["codes"]
    raw_text = open(CONFIG, encoding="utf-8").read()
    config_hash = compute_config_hash(raw_text)
    universe_hash = compute_universe_hash(universe)

    benchmark_db_path = bt.get("benchmark_db_path")
    if not os.path.isfile(benchmark_db_path):
        bt["benchmark_code"] = None
        print("[warn] benchmark db missing, benchmark disabled:", benchmark_db_path)

    rows = {}
    for src in ("astock", "gpsj"):
        print("=" * 60)
        print("数据源: %s" % src)
        if src == "astock":
            reader = AstockParquetReader(bt.get("path") or "E:/astock/daily/stock_daily.parquet",
                                         adjustment=bt.get("adjustment", "hfq"))
        else:
            reader = GpsjDuckDBReader(adjustment=bt.get("adjustment", "hfq"))
        try:
            result = run_backtest(
                reader=reader,
                universe=universe,
                start_date=START,
                end_date=END,
                strategy_config=strat_cfg,
                execution_cfg=exec_cfg,
                initial_cash=float(bt.get("initial_cash", 100000.0)),
                benchmark_code=bt.get("benchmark_code"),
                benchmark_db_path=benchmark_db_path,
                config_name=bt["name"] + "_" + src,
                config_hash=config_hash,
                universe_hash=universe_hash,
                strategy_name=cfg.get("strategy", "atr_lowvol"),
                trading_model=cfg.get("trading_model", "next_open"),
            )
        finally:
            reader.close()
        perf = result["summary"]["performance"]
        rows[src] = perf
        print("  total_return=%.2f%%  cagr=%.2f%%  linear=%.2f%%  mdd=%.2f%%  sharpe=%.3f  "
              "n_trades=%d  win_rate=%.1f%%  turnover=%.2f"
              % (perf["total_return"] * 100, perf["cagr"] * 100, perf["annual_return"] * 100,
                 perf["max_drawdown"] * 100, perf["sharpe"], perf["n_trades"],
                 perf["win_rate"] * 100, perf.get("annual_turnover", 0)))

    print("=" * 60)
    print("2025 年 ATR MAX5 两源对比")
    print("%-8s %10s %10s %10s %8s %9s %7s %7s %9s" %
          ("源", "总收益", "CAGR", "线性年化", "回撤", "夏普", "交易", "胜率", "换手"))
    for src in ("astock", "gpsj"):
        p = rows[src]
        print("%-8s %9.2f%% %9.2f%% %9.2f%% %7.2f%% %8.3f %6d %6.1f%% %8.2f" % (
            src, p["total_return"] * 100, p["cagr"] * 100, p["annual_return"] * 100,
            p["max_drawdown"] * 100, p["sharpe"], p["n_trades"],
            p["win_rate"] * 100, p.get("annual_turnover", 0)))

    # 对比差异
    a, g = rows["astock"], rows["gpsj"]
    print("-" * 60)
    print("差异 (gpsj - astock): total=%+.2fpp  cagr=%+.2fpp  mdd=%+.2fpp  sharpe=%+.3f  trades=%+d"
          % ((g["total_return"] - a["total_return"]) * 100, (g["cagr"] - a["cagr"]) * 100,
             (g["max_drawdown"] - a["max_drawdown"]) * 100, g["sharpe"] - a["sharpe"],
             g["n_trades"] - a["n_trades"]))


if __name__ == "__main__":
    main()