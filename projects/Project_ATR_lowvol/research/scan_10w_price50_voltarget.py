# coding: utf-8
"""T-20260815-001: 10万/8只+价<50 实际部署配置上跑 vol_target 无杠杆扫描。

基配置：projects/Project_ATR_lowvol/config/atr_10w_price50.yaml
        （10万/8只/等权/无杠杆/真实价<50/2019-2026）
扫描：  vol_target = 0 / 0.10 / 0.15 / 0.18 / 0.20
目的：  看对 -24.8% 回撤的压降与收益代价，再定 8 只部署是否值得实现 VT。
口径：  与昨日 20260814-173534 基线完全一致（仅叠加 vol_target），无杠杆时
        target_leverage=1.0 为硬上限，VT 只能向下缩敞口（削回撤），不能加杠杆。
用法：  py scripts/.. / 直接 python 运行；产物写到 D:/QuantLab/reports/。
"""
import json
import os
import sys

import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.hashing import compute_config_hash
from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe

BASE_CONFIG = r"D:/QuantLab/projects/Project_ATR_lowvol/config/atr_10w_price50.yaml"
VOL_TARGETS = [0.0, 0.10, 0.15, 0.18, 0.20]


def main():
    with open(BASE_CONFIG, "r", encoding="utf-8") as f:
        base = yaml.safe_load(f.read())

    bt = base["backtest"]
    data_cfg = base["data"]
    universe_cfg = base["universe"]
    exec_cfg = base["execution"]
    strat_cfg = dict(base["strategy_params"])

    uni = load_universe(universe_cfg["csv"])
    universe = uni["codes"]
    reader = AstockParquetReader(data_cfg["path"], adjustment=data_cfg["adjustment"])

    rows = []
    try:
        for vt in VOL_TARGETS:
            cfg = dict(strat_cfg)
            cfg["vol_target"] = vt
            name = "atr_10w_price50_vt%g" % vt if vt > 0 else "atr_10w_price50"
            result = run_backtest(
                reader=reader,
                universe=universe,
                start_date=bt["start_date"],
                end_date=bt["end_date"],
                strategy_config=cfg,
                execution_cfg=exec_cfg,
                initial_cash=float(bt["initial_cash"]),
                aux_data=None,
                benchmark_code=bt["benchmark_code"],
                benchmark_db_path=bt["benchmark_db_path"],
                config_name=name,
                config_hash=compute_config_hash(yaml.safe_dump(cfg, allow_unicode=True)),
                universe_hash="",
                strategy_name=base.get("strategy", "atr_lowvol"),
                trading_model=base.get("trading_model", "next_open"),
                industry_map=None,
            )
            rd = report.write_all(result, config_name=name)
            p = result["summary"]["performance"]
            rows.append((vt, rd, p))
            print("vt=%s  total=%.1f%%  annual=%.2f%%  dd=%.2f%%  sharpe=%.3f  n_trades=%d"
                  % (vt, p["total_return"] * 100, p["annual_return"] * 100,
                     p["max_drawdown"] * 100, p["sharpe"], p["n_trades"]))
    finally:
        reader.close()

    with open(os.path.join(PROJECT_ROOT, "reports",
                           "voltarget_10w_price50_scan.json"), "w", encoding="utf-8") as f:
        json.dump([{"vt": vt, "dir": rd, "perf": p}
                   for vt, rd, p in rows], f, ensure_ascii=False, indent=2)
    print("done -> D:/QuantLab/reports/voltarget_10w_price50_scan.json")


if __name__ == "__main__":
    main()
