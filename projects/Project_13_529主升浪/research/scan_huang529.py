# coding: utf-8
"""黄氏529主升浪精选 —— v2 配置矩阵扫描运行器。

矩阵维度（用户 2026-08-15 拍板）：
  ① 持有期 60→20 天（fwd20 统计最优）
  ② 止损 -8→-12%（突破票先跌后涨频繁误杀）
  ③ n_hold 8/12/16 扫描（信号日均 5.5 只）
  ④ MA200 门控 hold 模式（只挡新买不强制清仓）
  ⑤ MA200 门控 exit + 恢复日快速回补（signal_window=20）

输出：reports/ 下各配置报告目录 + 汇总对比表。
用法：python scan_huang529.py
"""
import json
import os
import sys

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
BASE_CONFIG = "D:/QuantLab/projects/Project_13_529主升浪/config/huang529_v2.yaml"
SIGNAL_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16.json"
MARKET_MA200 = "D:/QuantLab/projects/Project_13_529主升浪/research/market_ma200.json"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_SUMMARY = "D:/QuantLab/projects/Project_13_529主升浪/results/v2矩阵扫描汇总_20260815.json"

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe
import yaml

# (名称, 参数覆盖)
VARIANTS = [
    ("v2_n8_h20_s12",        {"n_hold": 8,  "max_holding_days": 20, "stop_loss": -0.12, "market_gate": 0}),
    ("v2_n12_h20_s12",       {"n_hold": 12, "max_holding_days": 20, "stop_loss": -0.12, "market_gate": 0}),
    ("v2_n16_h20_s12",       {"n_hold": 16, "max_holding_days": 20, "stop_loss": -0.12, "market_gate": 0}),
    ("v2_n8_gate_hold",      {"n_hold": 8,  "max_holding_days": 20, "stop_loss": -0.12, "market_gate": 1, "gate_mode": "hold"}),
    ("v2_n8_gate_exit_refill",{"n_hold": 8, "max_holding_days": 20, "stop_loss": -0.12, "market_gate": 1, "gate_mode": "exit", "signal_window": 20}),
]


def run_one(name, overrides, results_dir):
    with open(BASE_CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read())
    bt = cfg["backtest"]
    data_cfg = cfg["data"]
    exec_cfg = cfg["execution"]
    strat_cfg = dict(cfg["strategy_params"])
    strat_cfg.update(overrides)
    bt = dict(bt)
    bt["name"] = name

    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(data_cfg["path"], adjustment=data_cfg.get("adjustment", "raw"))

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
            execution_cfg=exec_cfg,
            initial_cash=float(bt.get("initial_cash", 1_000_000.0)),
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
    perf = result["summary"]["performance"]
    print("[%s] %s" % (name, rd))
    print("  total_return   = %.4f" % perf.get("total_return", 0))
    print("  annual_return  = %.4f" % perf.get("annual_return", 0))
    print("  max_drawdown   = %.4f" % perf.get("max_drawdown", 0))
    print("  sharpe         = %.4f" % perf.get("sharpe", 0))
    print("  n_trades       = %d" % perf.get("n_trades", 0))
    return {"name": name, "dir": rd,
            "total_return": perf.get("total_return", 0),
            "annual_return": perf.get("annual_return", 0),
            "max_drawdown": perf.get("max_drawdown", 0),
            "sharpe": perf.get("sharpe", 0),
            "n_trades": perf.get("n_trades", 0)}


def main():
    summary = []
    for name, overrides in VARIANTS:
        try:
            summary.append(run_one(name, overrides, REPORT_ROOT))
        except Exception as e:
            import traceback
            traceback.print_exc()
            summary.append({"name": name, "error": str(e)})
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("\n=== 汇总 ===")
    for s in summary:
        if "error" in s:
            print("%-24s ERROR %s" % (s["name"], s["error"]))
        else:
            print("%-24s total=%7.2f%% annual=%6.2f%% mdd=%7.2f%% sharpe=%.3f trades=%d"
                  % (s["name"], 100 * s["total_return"], 100 * s["annual_return"],
                     100 * s["max_drawdown"], s["sharpe"], s["n_trades"]))
    print("汇总: %s" % OUT_SUMMARY)


if __name__ == "__main__":
    main()