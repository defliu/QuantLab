# coding: utf-8
"""B1 trail15 组回测（仅 trail15 三段，base 已跑完）"""
import json, os, sys, time

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe
import yaml

BASE_CONFIG = "D:/QuantLab/projects/Project_13_529主升浪/config/huang529_v2.yaml"
SIGNAL_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16.json"
MARKET_MA200 = "D:/QuantLab/projects/Project_13_529主升浪/research/market_ma200.json"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_DIR = "D:/QuantLab/projects/Project_13_529主升浪/results"

SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

TRAIL15_OVERRIDES = {
    "n_hold": 12,
    "max_holding_days": 60,
    "stop_loss": -0.12,
    "market_gate": 0,
    "gate_mode": "exit",
    "max_single_pct": 1.0 / 12,
    "signal_window": 1,
    "trailing_stop": 0.15,
}

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s, flush=True)
    _log.append(s)


def _cfg_for(name, start, end, overrides):
    with open(BASE_CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read())
    bt = dict(cfg["backtest"])
    bt["name"] = name
    bt["start_date"] = start
    bt["end_date"] = end
    strat_cfg = dict(cfg["strategy_params"])
    strat_cfg.update(overrides)
    return cfg, bt, strat_cfg


def run_one(name, start, end, overrides):
    cfg, bt, strat_cfg = _cfg_for(name, start, end, overrides)
    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(cfg["data"]["path"],
                                 adjustment=cfg["data"].get("adjustment", "raw"))
    with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
        signal_table = json.load(f)
    with open(MARKET_MA200, "r", encoding="utf-8") as f:
        market_ma200 = json.load(f)

    report.set_results_dir(REPORT_ROOT)
    try:
        result = run_backtest(
            reader=reader, universe=universe,
            start_date=bt["start_date"], end_date=bt["end_date"],
            strategy_config=strat_cfg, execution_cfg=cfg["execution"],
            initial_cash=float(bt.get("initial_cash", 1000000.0)),
            aux_data={"huang_529_signals": signal_table,
                      "huang_529_market_ma200": market_ma200},
            benchmark_code=bt.get("benchmark_code"),
            benchmark_db_path=bt.get("benchmark_db_path"),
            config_name=name, strategy_name="huang_529",
            trading_model="next_open",
        )
    finally:
        reader.close()

    rd = report.write_all(result, config_name=name)
    p = result["summary"]["performance"]
    log("[%s] dir=%s" % (name, rd))
    log("  total=%7.2f%%  cagr=%6.2f%%(linear=%6.2f%%)  mdd=%7.2f%%  sharpe=%.3f  cagr_calmar=%.3f  trades=%d"
        % (100 * p["total_return"], 100 * p.get("cagr", p["annual_return"]),
           100 * p["annual_return"], 100 * p["max_drawdown"], p["sharpe"],
           p.get("cagr_calmar") or 0, p["n_trades"]))
    return {
        "name": name,
        "total_return": p["total_return"],
        "annual_return": p["annual_return"],
        "cagr": p.get("cagr", p["annual_return"]),
        "cagr_calmar": p.get("cagr_calmar"),
        "max_drawdown": p["max_drawdown"],
        "sharpe": p["sharpe"],
        "win_rate": p["win_rate"],
        "n_trades": p["n_trades"],
    }


def main():
    t_start = time.time()
    log("======== B1 trail15 三段回测 ========")

    results = {}
    for seg, start, end in SEGMENTS:
        name = "B1_trail15_%s" % seg
        log("\n--- %s ---" % name)
        try:
            row = run_one(name, start, end, TRAIL15_OVERRIDES)
            results[seg] = row
        except Exception as e:
            import traceback
            traceback.print_exc()
            results[seg] = {"error": str(e)}

    # 保存
    json_path = os.path.join(OUT_DIR, "B1_trail15_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    log("\n结果: %s" % json_path)
    log("总用时 %.0fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()
