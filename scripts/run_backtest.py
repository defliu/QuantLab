# coding: utf-8
"""CLI runner for QuantLab backtest engine.

Usage:
    python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml

Reads yaml from E:/QuantLab/config/, reads E:/astock parquet (READ-ONLY),
writes 6 result files to E:/QuantLab/reports/<run_id>_<config>/.

Boundaries:
  - Reads E:/astock (READ-ONLY)
  - Writes only under results dir (default E:/QuantLab/reports)
  - Never imports xtquant / passorder
"""
import argparse
import logging
import os
import sys

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.hashing import compute_config_hash, compute_universe_hash
from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe

log = logging.getLogger("run_backtest")


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    cfg = yaml.safe_load(text)
    return cfg, text


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="QuantLab backtest engine runner")
    parser.add_argument("--config", required=True,
                        help="path to yaml config (e.g. config/atr_lowvol_fw.yaml)")
    parser.add_argument("--results-dir", default=None,
                        help="override report output dir (default E:/QuantLab/reports)")
    args = parser.parse_args(argv)

    cfg, raw_text = _load_yaml(args.config)
    bt = cfg.get("backtest", {})
    data_cfg = cfg.get("data", {})
    universe_cfg = cfg.get("universe", {})
    exec_cfg = cfg.get("execution", {})
    strat_cfg = cfg.get("strategy_params", {})

    # 顶层 strategy + trading_model（扁平注册名，如 atr_lowvol）
    v_strategy_name = cfg.get("strategy") or "atr_lowvol"
    v_trading_model = cfg.get("trading_model") or exec_cfg.get("price", "next_open")

    config_name = bt.get("name", "baseline")
    config_hash = compute_config_hash(raw_text)

    universe_csv = universe_cfg.get("csv")
    if not universe_csv:
        raise ValueError("yaml universe.csv must be set")
    universe_csv = os.path.abspath(universe_csv)
    uni = load_universe(universe_csv)
    universe = uni["codes"]
    universe_hash = compute_universe_hash(universe)
    log.info("universe loaded: %d codes from %s", len(universe), universe_csv)

    db_path = data_cfg.get("path")
    adjustment = data_cfg.get("adjustment")
    if adjustment is not None and adjustment not in ("raw", "qfq", "hfq"):
        raise ValueError("data.adjustment must be one of raw/qfq/hfq, got: %s" % adjustment)

    reader = AstockParquetReader(db_path, adjustment=adjustment or "raw")

    # v0.5: build industry map only when industry_cap overlay is requested
    industry_map = None
    if float(strat_cfg.get("industry_cap", 0) or 0) > 0:
        try:
            from data.industry_map import load_industry_map
            industry_map = load_industry_map()
            log.info("industry_cap enabled: industry_map loaded (%d codes)",
                     len(industry_map))
        except Exception as e:
            log.warning("industry_map load failed: %s; industry_cap disabled", e)

    if args.results_dir:
        report.set_results_dir(os.path.abspath(args.results_dir))

    try:
        result = run_backtest(
            reader=reader,
            universe=universe,
            start_date=bt["start_date"],
            end_date=bt["end_date"],
            strategy_config=strat_cfg,
            execution_cfg=exec_cfg,
            initial_cash=float(bt.get("initial_cash", 1_000_000.0)),
            aux_data=None,
            benchmark_code=bt.get("benchmark_code"),
            benchmark_db_path=bt.get("benchmark_db_path"),
            config_name=config_name,
            config_hash=config_hash,
            universe_hash=universe_hash,
            strategy_name=v_strategy_name,
            trading_model=v_trading_model,
            industry_map=industry_map,
        )
    finally:
        reader.close()

    rd = report.write_all(result, config_name=config_name)
    perf = result["summary"]["performance"]
    log.info("backtest complete: %s", rd)
    print("results_dir: %s" % rd)
    print("total_return   = %.4f" % perf.get("total_return", 0))
    print("annual_return  = %.4f" % perf.get("annual_return", 0))
    print("max_drawdown   = %.4f" % perf.get("max_drawdown", 0))
    print("sharpe         = %.4f" % perf.get("sharpe", 0))
    print("n_trades       = %d" % perf.get("n_trades", 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
