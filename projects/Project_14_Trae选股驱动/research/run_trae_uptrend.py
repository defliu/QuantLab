# coding: utf-8
"""Trae 主升浪启动前/中段选股 —— 回测运行器（注入预计算信号表 aux_data）。

用法：
  python run_trae_uptrend.py trae_uptrend_base
"""
import json
import logging
import os
import sys

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
SIGNAL_TABLE = "D:/QuantLab/projects/Project_14_Trae选股驱动/research/signal_table_trae_top16.json"
MARKET_MA200 = "D:/QuantLab/projects/Project_14_Trae选股驱动/research/market_ma200.json"
REPORT_ROOT = "D:/QuantLab/reports"

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe
import yaml

log = logging.getLogger("run_trae_uptrend")

CONFIGS = {
    "trae_uptrend_base": "D:/QuantLab/projects/Project_14_Trae选股驱动/config/trae_uptrend_base.yaml",
}


def run_one(config_path, results_dir):
    with open(config_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read())
    bt = cfg["backtest"]
    data_cfg = cfg["data"]
    exec_cfg = cfg["execution"]
    strat_cfg = cfg["strategy_params"]

    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(data_cfg["path"], adjustment=data_cfg.get("adjustment", "raw"))

    with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
        signal_table = json.load(f)
    with open(MARKET_MA200, "r", encoding="utf-8") as f:
        market_ma200 = json.load(f)

    print("[aux] signal_table: %d days, market_ma200: %d days"
          % (len(signal_table), len(market_ma200)))

    report.set_results_dir(results_dir)
    try:
        result = run_backtest(
            reader=reader,
            universe=universe,
            start_date=bt["start_date"],
            end_date=bt["end_date"],
            strategy_config=strat_cfg,
            execution_cfg=exec_cfg,
            initial_cash=float(bt.get("initial_cash", 1_000_000.0)),
            aux_data={"trae_uptrend_signals": signal_table,
                      "trae_market_ma200": market_ma200},
            benchmark_code=bt.get("benchmark_code"),
            benchmark_db_path=bt.get("benchmark_db_path"),
            config_name=bt.get("name", "trae_uptrend"),
            strategy_name="trae_main_uptrend",
            trading_model="next_open",
        )
    finally:
        reader.close()

    rd = report.write_all(result, config_name=bt.get("name", "trae_uptrend"))
    perf = result["summary"]["performance"]
    print("[%s] %s" % (bt["name"], rd))
    print("  total_return   = %.4f" % perf.get("total_return", 0))
    print("  cagr           = %.4f (线性 annual %.4f)"
          % (perf.get("cagr", perf.get("annual_return", 0)), perf.get("annual_return", 0)))
    print("  max_drawdown   = %.4f" % perf.get("max_drawdown", 0))
    print("  sharpe         = %.4f" % perf.get("sharpe", 0))
    print("  n_trades       = %d" % perf.get("n_trades", 0))
    return result


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    name = (argv or sys.argv[1:])[0] if len(sys.argv) > 1 else "trae_uptrend_base"
    targets = list(CONFIGS.keys()) if name == "all" else [name]
    for t in targets:
        if t not in CONFIGS:
            print("unknown config: %s (available: %s)" % (t, list(CONFIGS.keys())))
            return 1
        run_one(CONFIGS[t], REPORT_ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
