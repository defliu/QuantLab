# coding: utf-8
"""T-20260817-001 T1 —— P13 引擎级 trail15 验证（n12_h60_s12 ± trailing_stop=0.15）。

任务书预注册规则（跑前生效，防挑参数）：
  trail15 相对 base 全样本 CAGR 改善 >= +0.3pp，
  且 2023-2026 段 CAGR 不劣化 > 0.3pp、最大回撤不恶化 > 2pp
  -> 部署含 trail15；否则部署纯 base。不做 trail 参数扫描。

口径 = scan_v3_1overN.py 的 n12_h60_s12 配置（max_single_pct=1/12，
signal_window=1，top16 信号表，门控关），± trailing_stop=0.15。

用法：python research/scan_trail15.py
产物：reports/trail15_* 报告目录（2 配置 × 3 段）+ results/trail15_scan.json
"""
import json
import os
import sys

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
OUT_JSON = "D:/QuantLab/projects/Project_13_529主升浪/results/trail15_scan.json"

SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

VARIANTS = [
    ("trail15_n12_base", {"n_hold": 12, "max_holding_days": 60, "stop_loss": -0.12,
                          "market_gate": 0, "gate_mode": "exit",
                          "max_single_pct": 1.0 / 12, "trailing_stop": None}),
    ("trail15_n12_t15", {"n_hold": 12, "max_holding_days": 60, "stop_loss": -0.12,
                         "market_gate": 0, "gate_mode": "exit",
                         "max_single_pct": 1.0 / 12, "trailing_stop": 0.15}),
]


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


def run_one(name, start, end, overrides, results_dir):
    cfg, bt, strat_cfg = _cfg_for(name, start, end, overrides)
    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(cfg["data"]["path"],
                                 adjustment=cfg["data"].get("adjustment", "raw"))
    with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
        signal_table = json.load(f)
    with open(MARKET_MA200, "r", encoding="utf-8") as f:
        market_ma200 = json.load(f)

    report.set_results_dir(results_dir)
    try:
        result = run_backtest(
            reader=reader,
            universe=universe,
            start_date=bt["start_date"],
            end_date=bt["end_date"],
            strategy_config=strat_cfg,
            execution_cfg=cfg["execution"],
            initial_cash=float(bt.get("initial_cash", 1000000.0)),
            aux_data={"huang_529_signals": signal_table,
                      "huang_529_market_ma200": market_ma200},
            benchmark_code=bt.get("benchmark_code"),
            benchmark_db_path=bt.get("benchmark_db_path"),
            config_name=name,
            strategy_name="huang_529",
            trading_model="next_open",
        )
    finally:
        reader.close()

    rd = report.write_all(result, config_name=name)
    p = result["summary"]["performance"]
    print("[%s] %s" % (name, rd))
    print("  total=%7.2f%%  cagr=%6.2f%%(linear=%6.2f%%)  mdd=%7.2f%%  sharpe=%.3f  cagr_calmar=%.3f  trades=%d"
          % (100 * p["total_return"], 100 * p.get("cagr", p["annual_return"]),
             100 * p["annual_return"], 100 * p["max_drawdown"], p["sharpe"],
             p.get("cagr_calmar") or 0, p["n_trades"]))
    return {
        "name": name, "dir": rd,
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
    results = []
    for variant_name, overrides in VARIANTS:
        for seg, start, end in SEGMENTS:
            name = variant_name if seg == "full" else variant_name + "_" + seg
            try:
                row = run_one(name, start, end, overrides, REPORT_ROOT)
                row["segment"] = seg
                results.append(row)
            except Exception as e:
                import traceback
                traceback.print_exc()
                results.append({"name": name, "segment": seg, "error": str(e)})
            with open(OUT_JSON, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print("progress %d/%d -> %s" % (len(results), len(VARIANTS) * len(SEGMENTS), OUT_JSON))
    print("\n=== 全部完成，共 %d 段 ===" % len(results))
    print("结果: %s" % OUT_JSON)


if __name__ == "__main__":
    main()
