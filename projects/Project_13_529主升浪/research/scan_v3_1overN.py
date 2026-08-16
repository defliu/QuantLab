# coding: utf-8
"""T-20260816-001 T3 —— P13 v3 矩阵 1/n 修正复跑 + 时段判定。

原扫描（v2v3参数矩阵扫描_20260816.md）所有 n_hold 变体共用基配置
`huang529_v2.yaml` 的 max_single_pct=0.125，n12/n16 未按 1/n 调整
（n16 实为 16 槽 × 12.5% 上限≈满仓，并非分散到 16 只各 6.25%）。
本脚本修正 max_single_pct = 1.0/n_hold 重跑 6 配置 × 3 时段：

  n ∈ {8, 12, 16} × {无门控, gate_hold}，其余冻结
  （max_holding_days=60 / stop_loss=-0.12 / signal_window=1 / top16 信号表）

  3 时段 = 全样本(2019-2026) / 2019-2022 / 2023-2026（sample start/end 配置）

红线#2：框架 run_backtest 纯内存、无 state_file，不存在残留污染；
        本任务不触碰 P10 runner 的 risk_state。

用法：python research/scan_v3_1overN.py
产物：reports/v3_1n_* 报告目录（每配置 3 段）+ results/v3_1overN_scan.json（增量写）
判定：python research/report_v3_1overN.py 读该 json 出 results/v3_1overN复跑判定_20260816.md
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
OUT_JSON = "D:/QuantLab/projects/Project_13_529主升浪/results/v3_1overN_scan.json"

# 分段（全样本 + 两子段，sample start/end 配置）
SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

# 6 配置：n × {无门控, gate_hold}；max_single_pct = 1.0/n_hold（修正点）
VARIANTS = []
for n in (8, 12, 16):
    VARIANTS.append(("v3_1n_n%d_h60_s12" % n, {
        "n_hold": n, "max_holding_days": 60, "stop_loss": -0.12,
        "market_gate": 0, "gate_mode": "exit", "max_single_pct": 1.0 / n,
    }))
    VARIANTS.append(("v3_1n_n%d_gate_hold" % n, {
        "n_hold": n, "max_holding_days": 60, "stop_loss": -0.12,
        "market_gate": 1, "gate_mode": "hold", "max_single_pct": 1.0 / n,
    }))


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
            # 增量写盘：跑完一段就保存，避免长时间任务中断丢结果
            with open(OUT_JSON, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print("progress %d/%d -> %s" % (len(results), len(VARIANTS) * len(SEGMENTS), OUT_JSON))
    print("\n=== 全部完成，共 %d 段 ===" % len(results))
    print("结果: %s" % OUT_JSON)


if __name__ == "__main__":
    main()