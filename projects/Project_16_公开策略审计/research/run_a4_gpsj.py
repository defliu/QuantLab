# coding: utf-8
"""任务B A4 gpsj 交叉验证（任务书 §2.5 必跑）：随机抽 2025 自然年，
gpsj 行情 vs astock 行情跑同一 A4 引擎，CAGR/MDD 差异 >1pp 需排查。
财务 aux（total_mv/自建TTM/PIT净利同比/list_date）两源同源，共用 astock 构建。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, r"D:\QuantLab")

RESULTS = os.path.join(PROJ, "results")
os.makedirs(RESULTS, exist_ok=True)

from strategy.registry import get_strategy
from research.run_a4_engine import build_aux  # noqa: E402  (模块内已注册 cai_market_audit)
assert get_strategy("cai_market_audit") is not None


def main():
    print("=" * 70)
    print("任务B A4 gpsj 交叉验证（2025 自然年）")
    t0 = time.time()

    cfg_path = os.path.join(PROJ, "config", "cai_market_a4.yaml")
    import yaml
    cfg = yaml.safe_load(open(cfg_path, encoding="utf-8"))
    bt = dict(cfg["backtest"])
    bt["start_date"] = "2025-01-01"
    bt["end_date"] = "2025-12-31"

    from backtest.engine import run_backtest
    from data.astock_reader import AstockParquetReader
    from data.gpsj_reader import GpsjDuckDBReader
    from data.universe import load_universe

    uni = load_universe(cfg["universe"]["csv"])
    universe = uni["codes"]

    benchmark_db_path = bt.get("benchmark_db_path")
    if not os.path.isfile(benchmark_db_path):
        bt["benchmark_code"] = None
        print("[warn] benchmark db missing, disabled:", benchmark_db_path)

    aux = build_aux()
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
                start_date=bt["start_date"],
                end_date=bt["end_date"],
                strategy_config=cfg["strategy_params"],
                execution_cfg=cfg["execution"],
                initial_cash=float(bt.get("initial_cash", 1000000.0)),
                aux_data=aux,
                benchmark_code=bt.get("benchmark_code"),
                benchmark_db_path=benchmark_db_path,
                config_name=bt["name"] + "_" + src,
                strategy_name=cfg.get("strategy", "cai_market_audit"),
                trading_model=cfg.get("trading_model", "next_open"),
            )
        finally:
            reader.close()
        perf = result["summary"]["performance"]
        rows[src] = perf
        # 落盘 trades 供选股对比
        try:
            import csv
            tpath = os.path.join(RESULTS, "taskb_A4_2025_%s_trades.csv" % src)
            with open(tpath, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(list(result["trades"][0].keys()) if result["trades"] else [])
                for t in result["trades"]:
                    w.writerow([str(t.get(k, "")) for k in (result["trades"][0].keys() if result["trades"] else [])])
            print("  saved %s" % tpath)
        except Exception as e:
            print("  trade dump failed: %s" % e)
        print("  total_return=%.2f%%  cagr=%.2f%%  linear=%.2f%%  mdd=%.2f%%  sharpe=%.3f  "
              "n_trades=%d  win_rate=%.1f%%"
              % (perf["total_return"] * 100, perf["cagr"] * 100, perf["annual_return"] * 100,
                 perf["max_drawdown"] * 100, perf["sharpe"], perf["n_trades"],
                 perf["win_rate"] * 100))

    print("=" * 60)
    print("2025 年 A4 两源对比")
    a, g = rows["astock"], rows["gpsj"]
    print("差异 (gpsj - astock): total=%+.2fpp  cagr=%+.2fpp  mdd=%+.2fpp  sharpe=%+.3f  trades=%+d"
          % ((g["total_return"] - a["total_return"]) * 100, (g["cagr"] - a["cagr"]) * 100,
             (g["max_drawdown"] - a["max_drawdown"]) * 100, g["sharpe"] - a["sharpe"],
             g["n_trades"] - a["n_trades"]))

    import json
    out = {
        "year": "2025",
        "astock": {k: rows["astock"].get(k) for k in
                   ["total_return", "cagr", "annual_return", "max_drawdown", "sharpe", "n_trades"]},
        "gpsj": {k: rows["gpsj"].get(k) for k in
                 ["total_return", "cagr", "annual_return", "max_drawdown", "sharpe", "n_trades"]},
    }
    with open(os.path.join(RESULTS, "taskb_A4_gpsj_compare_2025.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved taskb_A4_gpsj_compare_2025.json, 用时 %.0fs" % (time.time() - t0))


if __name__ == "__main__":
    main()