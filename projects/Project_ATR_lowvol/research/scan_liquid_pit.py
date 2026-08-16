# coding: utf-8
"""PIT 流动性池测试：逐季度按"当时 circ_mv≥30亿"动态截断宇宙，消除静态池的幸存者偏差。

对比静态 2026-07-31 流动性池（45.49% 可能被幸存者偏差高估）——
本脚本用引擎 universe_by_date 做 PIT 截断，得到干净口径。
"""
import json
import os
import sys

import pandas as pd

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe

CFG = r"D:/QuantLab/projects/Project_ATR_lowvol/config/atr_10w_price50.yaml"
CMV_MIN = 300000.0  # 30亿（万元）


def main():
    import yaml
    cfg = yaml.safe_load(open(CFG, encoding="utf-8"))
    bt, data_cfg, uni_cfg, exec_cfg, strat_cfg = (
        cfg["backtest"], cfg["data"], cfg["universe"], cfg["execution"], cfg["strategy_params"])

    reader = AstockParquetReader(data_cfg["path"], adjustment=data_cfg["adjustment"])
    universe = load_universe(uni_cfg["csv"])["codes"]
    calendar = reader.trading_calendar(bt["start_date"], bt["end_date"])

    # 季度首个交易日
    quarter_starts = []
    last_q = None
    for d in calendar:
        q = d[:7]  # YYYY-MM
        qkey = (int(d[:4]), (int(d[5:7]) - 1) // 3)
        if qkey != last_q:
            quarter_starts.append(d)
            last_q = qkey

    # 读 circ_mv，构建 date×code 透视 + ffill（最近已知市值）
    df = pd.read_parquet("E:/astock/daily/stock_daily.parquet", columns=["circ_mv"])
    piv = df["circ_mv"].unstack("ts_code")
    piv = piv.sort_index().ffill()

    universe_by_date = {}
    n_liq = []
    for qd in quarter_starts:
        if qd not in piv.index:
            prev = piv.loc[:qd]
            if len(prev) == 0:
                continue
            row = prev.iloc[-1]
        else:
            row = piv.loc[qd]
        row = row.dropna()
        liq = [c for c in row.index if row[c] >= CMV_MIN]
        universe_by_date[qd] = liq
        n_liq.append(len(liq))
    print("季度快照数:", len(universe_by_date), " 流动性池规模 min/avg/max = %d/%d/%d"
          % (min(n_liq), sum(n_liq)//len(n_liq), max(n_liq)))

    result = run_backtest(
        reader=reader, universe=universe,
        start_date=bt["start_date"], end_date=bt["end_date"],
        strategy_config=strat_cfg, execution_cfg=exec_cfg,
        initial_cash=float(bt["initial_cash"]), aux_data=None,
        benchmark_code=bt["benchmark_code"], benchmark_db_path=bt["benchmark_db_path"],
        config_name="atr_10w_price50_liquid_pit",
        config_hash="", universe_hash="",
        strategy_name=cfg.get("strategy", "atr_lowvol"),
        trading_model=cfg.get("trading_model", "next_open"),
        industry_map=None,
        universe_by_date=universe_by_date,
    )
    rd = report.write_all(result, config_name="atr_10w_price50_liquid_pit")
    p = result["summary"]["performance"]
    print("results_dir:", rd)
    print("total_return  = %.4f" % p["total_return"])
    print("cagr          = %.4f (线性 annual %.4f)" % (p.get("cagr", p["annual_return"]),
                                                       p["annual_return"]))
    print("max_drawdown  = %.4f" % p["max_drawdown"])
    print("sharpe        = %.4f" % p["sharpe"])
    print("n_trades      = %d" % p["n_trades"])


if __name__ == "__main__":
    main()
