# coding: utf-8
"""C2 回测续跑（信号表已生成，只需跑3段回测）"""
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
MARKET_MA200 = "D:/QuantLab/projects/Project_13_529主升浪/research/market_ma200.json"
GAP3_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16_gap3.json"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_DIR = "D:/QuantLab/projects/Project_13_529主升浪/results"

SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

n12_overrides = {
    "n_hold": 12, "max_holding_days": 60, "stop_loss": -0.12,
    "market_gate": 0, "gate_mode": "exit", "max_single_pct": 1.0 / 12,
    "signal_window": 1, "trailing_stop": None,
}

_log = []
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _log.append(s)


def run_one(name, start, end, overrides, stp):
    with open(BASE_CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read())
    bt = dict(cfg["backtest"])
    bt["name"] = name
    bt["start_date"] = start
    bt["end_date"] = end
    strat_cfg = dict(cfg["strategy_params"])
    strat_cfg.update(overrides)

    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(cfg["data"]["path"],
                                 adjustment=cfg["data"].get("adjustment", "raw"))
    with open(stp, "r", encoding="utf-8") as f:
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
    log("  total=%7.2f%%  cagr=%6.2f%%(linear=%6.2f%%)  mdd=%7.2f%%  sharpe=%.3f  trades=%d"
        % (100 * p["total_return"], 100 * p.get("cagr", p["annual_return"]),
           100 * p["annual_return"], 100 * p["max_drawdown"], p["sharpe"],
           p["n_trades"]))
    return {
        "name": name, "total_return": p["total_return"],
        "annual_return": p["annual_return"],
        "cagr": p.get("cagr", p["annual_return"]),
        "cagr_calmar": p.get("cagr_calmar"),
        "max_drawdown": p["max_drawdown"], "sharpe": p["sharpe"],
        "win_rate": p["win_rate"], "n_trades": p["n_trades"],
    }


def main():
    t0 = time.time()
    log("======== C2 gap3 回测续跑 ========")

    results = {}
    for seg, start, end in SEGMENTS:
        name = "C2_gap3_%s" % seg
        log("\n--- %s ---" % name)
        try:
            row = run_one(name, start, end, n12_overrides, GAP3_TABLE)
            results[seg] = row
        except Exception as e:
            import traceback
            traceback.print_exc()
            results[seg] = {"error": str(e)}

    # 判定
    base_cagrs = {"full": 0.0610, "1922": 0.1422, "2326": 0.0677}
    full_cagr = results.get("full", {}).get("cagr", 0)
    late_cagr = results.get("2326", {}).get("cagr", 0)
    cagr_diff = (full_cagr - base_cagrs["full"]) * 100
    late_diff = (late_cagr - base_cagrs["2326"]) * 100

    log("\n全样本 CAGR: %.2f%% (base %.2f%%, %+.2fpp)" % (full_cagr*100, base_cagrs["full"]*100, cagr_diff))
    log("2023-2026 CAGR: %.2f%% (base %.2f%%, %+.2fpp)" % (late_cagr*100, base_cagrs["2326"]*100, late_diff))

    c2_pass = cagr_diff >= -0.3 and late_diff >= 0
    log("判定: %s" % ("PASS" if c2_pass else "FAIL"))

    # 写报告
    lines = []
    lines.append("# C2 529跳空>3%不追（2026-08-18）\n")
    lines.append("> 通宵批次任务书 T-20260817-004\n")
    lines.append("## 对比表\n")
    lines.append("| 组 | 时段 | 总收益 | CAGR(线性) | 回撤 | 夏普 | 交易数 |")
    lines.append("|---|---|---|---|---|---|---|")
    base_data = {
        "full": {"total_return": 0.5314, "cagr": 0.0610, "annual_return": 0.0738, "max_drawdown": -0.2407, "sharpe": 0.416, "n_trades": 837},
        "1922": {"total_return": 0.6698, "cagr": 0.1422, "annual_return": 0.1736, "max_drawdown": -0.2197, "sharpe": 0.769, "n_trades": 454},
        "2326": {"total_return": 0.2452, "cagr": 0.0677, "annual_return": 0.0733, "max_drawdown": -0.1966, "sharpe": 0.484, "n_trades": 374},
    }
    for sn, sk in [("全样本","full"),("2019-2022","1922"),("2023-2026","2326")]:
        b = base_data[sk]
        lines.append("| base | %s | %+.1f%% | %.2f%%(%.2f%%) | %.2f%% | %.3f | %d |" % (
            sn, b["total_return"]*100, b["cagr"]*100, b["annual_return"]*100,
            b["max_drawdown"]*100, b["sharpe"], b["n_trades"]))
        g = results.get(sk, {})
        if "error" not in g:
            lines.append("| gap3 | %s | %+.1f%% | %.2f%%(%.2f%%) | %.2f%% | %.3f | %d |" % (
                sn, g["total_return"]*100, g["cagr"]*100, g["annual_return"]*100,
                g["max_drawdown"]*100, g["sharpe"], g["n_trades"]))

    lines.append("\n## 判定\n")
    lines.append("规则: 全样本CAGR变化≥-0.3pp（基本无害）且2023-2026段改善")
    lines.append("- 全样本CAGR变化: %+.2fpp" % cagr_diff)
    lines.append("- 2023-2026段CAGR变化: %+.2fpp" % late_diff)
    lines.append("\n**判定: %s**" % ("PASS -> 建议纳入部署" if c2_pass else "FAIL -> 弃"))

    out_path = os.path.join(OUT_DIR, "C2_跳空不追_20260818.md")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp_path, out_path)
    log("报告: %s" % out_path)
    log("总用时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
