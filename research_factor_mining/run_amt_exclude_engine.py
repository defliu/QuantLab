# coding=utf-8
"""AMT_RATIO 合格域内排除 —— 真实约束回测（backtest/engine）基线 vs 域内排除版

任务: research_factor_mining/de_amt_exclude_task.md（方案A：策略侧单参数域内剔除）
对照: 简化版 +2.65pp（exclude_ablation_report.md）/ universe层 +0.57pp（amt_ablation_engine_report.md）

口径: atr_10w_price50（10万/8只/季频/ATR%<6/换手1-8%/真实价<50/质量+动量门控/止损-8%）
引擎: next_open / 滑点0.1% / 佣金万2.5 / 印花千0.5 / 涨停拒买 / 跌停拒卖 / 停牌拒单 / 整数手
区间: 2019-01-01 ~ 2026-09-11（与 universe 层复核完全一致，保证三口径可比）

用法:
  D:\\Python311\\python.exe research_factor_mining/run_amt_exclude_engine.py
"""
import json, os, sys, time, re
import pandas as pd

PROJECT_ROOT = "D:/QuantLab"
sys.path.insert(0, PROJECT_ROOT)

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe
import strategy.atr_lowvol_amt_exclude  # noqa: F401  显式注册新策略（autodiscover 亦会触发）

DAILY_PATH = r"D:/astock/daily/stock_daily.parquet"
REPORT_ROOT = "D:/QuantLab/reports"
HERE = os.path.dirname(os.path.abspath(__file__))
START, END = "2019-01-01", "2026-09-11"

_log = []
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _log.append(s)


BASE_STRATEGY_CONFIG = {
    "rebalance_freq": "quarterly",
    "n_hold": 8,
    "atr_win": 14,
    "atr_pct_max": 0.06,
    "turnover_min": 1.0,
    "turnover_max": 8.0,
    "quality_gate": 1,
    "momentum_gate": 1,
    "stop_loss": -0.08,
    "min_history": 252,
    "position_sizing": "equal",
    "max_positions": 8,
    "max_price": 50.0,
    "max_exclude_pct": 0.0,
}
EXECUTION_CFG = {
    "price": "next_open",
    "slippage": 0.001,
    "commission_rate": 0.00025,
    "tax_rate": 0.0005,
}


def run_one(reader, universe, strategy_name, strategy_config, name):
    t0 = time.time()
    report.set_results_dir(REPORT_ROOT)
    result = run_backtest(
        reader=reader,
        universe=universe,
        start_date=START,
        end_date=END,
        strategy_config=strategy_config,
        execution_cfg=EXECUTION_CFG,
        initial_cash=100000.0,
        benchmark_code="000300.SH",
        benchmark_db_path=None,
        config_name=name,
        strategy_name=strategy_name,
        trading_model="next_open",
    )
    rd = report.write_all(result, config_name=name)
    p = result["summary"]["performance"]
    # 从每日日志解析每次调仓的域内剔除数
    excl_by_rebal = []
    pat = re.compile(r"rebalance: (\d+) selected \(ATR%<=(?P<atr>[0-9.]+), amt_excl=(?P<excl>\d+)\)")
    for line in result["logs"]:
        m = pat.search(line)
        if m:
            excl_by_rebal.append({"date": line[:10], "excluded": int(m.group("excl"))})
    log("[%s] dir=%s  (%.0fs)" % (name, rd, time.time() - t0))
    log("  total=%7.2f%%  cagr=%6.2f%%  mdd=%7.2f%%  sharpe=%.3f  calmar=%.3f  trades=%d"
        % (100 * p["total_return"], 100 * p.get("cagr", p["annual_return"]),
           100 * p["max_drawdown"], p["sharpe"],
           p.get("cagr_calmar") or 0, p["n_trades"]))
    out = {
        "name": name, "strategy_name": strategy_name,
        "total_return": p["total_return"],
        "annual_return": p["annual_return"],
        "cagr": p.get("cagr", p["annual_return"]),
        "cagr_calmar": p.get("cagr_calmar"),
        "max_drawdown": p["max_drawdown"],
        "sharpe": p["sharpe"], "n_trades": p["n_trades"],
        "amt_excl_by_rebalance": excl_by_rebal,
    }
    return out


def main():
    t0 = time.time()
    log("======== AMT_RATIO 合格域内排除 真实约束回测（基线 vs 域内排除）========")
    log("运行: %s | 口径: atr_10w_price50(10万/8只/季频/真实约束) | 区间: %s~%s"
        % (time.strftime("%Y-%m-%d %H:%M:%S"), START, END))

    universe = load_universe("D:/QuantLab/data/universe_all_a.csv")["codes"]
    reader = AstockParquetReader(DAILY_PATH, adjustment="hfq")
    try:
        base = run_one(reader, universe, "atr_lowvol",
                       dict(BASE_STRATEGY_CONFIG), "amt_in_domain_base")
        excl_cfg = dict(BASE_STRATEGY_CONFIG)
        excl_cfg["amt_exclude_pct"] = 0.20
        excl = run_one(reader, universe, "atr_lowvol_amt_exclude",
                       excl_cfg, "amt_in_domain_excl")
    finally:
        reader.close()

    results = {"base": base, "excl": excl,
               "meta": {"start": START, "end": END,
                        "initial_cash": 100000.0,
                        "execution": EXECUTION_CFG,
                        "strategy_config_base": BASE_STRATEGY_CONFIG,
                        "amt_exclude_pct": 0.20,
                        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}}
    out_json = os.path.join(HERE, "amt_exclude_engine_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log("\n结果JSON: %s | 总用时 %.0fs" % (out_json, time.time() - t0))
    delta = (excl["cagr"] - base["cagr"]) * 100
    log("vs基线CAGR: %+.2fpp | 回撤变化: %+.2fpp"
        % (delta, (excl["max_drawdown"] - base["max_drawdown"]) * 100))


if __name__ == "__main__":
    main()
