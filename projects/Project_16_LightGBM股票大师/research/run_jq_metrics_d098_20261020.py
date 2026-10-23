# -*- coding: utf-8 -*-
"""deploy=0.98 档（现行实盘口径 RESERVE_CASH_PCT=0.02）三策略全指标重跑。

与 jq_metrics.py 完全相同，唯一差异：BT_DEPLOY_PCT=0.98，输出另存文件名。
用途：2026-10-19 引擎/成本修复后的实盘对齐口径数字。
"""
import os
import sys
import json
import datetime

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "research"))

os.environ["BT_DEPLOY_PCT"] = "0.98"

import jq_metrics as JM  # noqa: E402

out = {"meta": {"period": "2024-07-01 ~ 2026-08-14", "rf": JM.RF_ANN,
                "deploy_pct": 0.98,
                "cost": "佣金万2双边(最低5元)+印花税万5卖出+过户费万0.1沪深双边 | 滑点0.1%/边 | open→open可执行",
                "engine": "P16 自有 simulate()（非 backtest/engine.py）"},
       "strategies": {}}

for name, cfg in JM.CONFIGS.items():
    print(f"[{name}] 运行 ...", flush=True)
    trades, strat, bench = JM.run_cfg(cfg)
    m = JM.metrics(trades, strat, bench)
    m["tag"] = cfg["tag"]
    out["strategies"][name] = m
    print(f"  总收益 {m['total_return']*100:.2f}% | 年化 {m['annual_return']*100:.2f}% | "
          f"夏普 {m['sharpe']:.2f} | 回撤 {m['max_drawdown']*100:.1f}% | 交易 {m['n_trades']} | "
          f"胜率 {m['win_rate']*100:.1f}%", flush=True)

res_dir = os.path.join(PROJ, "research", "results")
os.makedirs(res_dir, exist_ok=True)
stamp = datetime.datetime.now().strftime("%Y%m%d")
out_path = os.path.join(res_dir, f"jq_metrics_{stamp}_d098.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False)
print("已写入", out_path)
