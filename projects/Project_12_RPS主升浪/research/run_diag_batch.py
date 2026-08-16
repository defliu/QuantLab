# coding: utf-8
"""批量跑诊断变体（2020-2021），汇总持仓天数 + 收益，定位"几乎全年空仓"根因。"""
import subprocess
import sys
import os
import pandas as pd
import json

QL = "D:/QuantLab"
PY = "C:/Users/Administrator/.workbuddy/binaries/python/versions/3.13.12/python.exe"
CONFIGS = [
    "diag_baseline",
    "diag_nogate",
    "diag_loose",
    "diag_rps80",
    "diag_nosector",
    "diag_loose_all",
]

results = []
for cfg_name in CONFIGS:
    cfg_path = "projects/Project_12_RPS主升浪/config/%s.yaml" % cfg_name
    log_path = "projects/Project_12_RPS主升浪/results/%s.log" % cfg_name
    print("\n=== Running %s ===" % cfg_name)
    ret = subprocess.run(
        [PY, "-m", "scripts.run_backtest", "--config", cfg_path],
        cwd=QL, capture_output=True, text=True)
    # 写日志
    with open(os.path.join(QL, log_path), "w", encoding="utf-8") as f:
        f.write(ret.stdout)
        f.write(ret.stderr)
    # 解析结果目录
    results_dir = None
    for line in ret.stdout.splitlines():
        if "results_dir:" in line:
            results_dir = line.split("results_dir:")[1].strip()
    print("  results_dir: %s (exit=%d)" % (results_dir, ret.returncode))
    if not results_dir or not os.path.isdir(results_dir):
        results.append({"config": cfg_name, "error": "no results dir"})
        continue
    # 提取指标
    summary_path = os.path.join(results_dir, "summary.json")
    eq_path = os.path.join(results_dir, "equity_curve.csv")
    summary = {}
    if os.path.isfile(summary_path):
        with open(summary_path, "r", encoding="utf-8") as f:
            summary = json.load(f)
    hold_days = None
    if os.path.isfile(eq_path):
        eq = pd.read_csv(eq_path)
        hold_days = int((eq["market_value"] > 100).sum())
    perf = summary.get("performance", {})
    results.append({
        "config": cfg_name,
        "total_return": perf.get("total_return"),
        "annual_return": perf.get("annual_return"),
        "cagr": perf.get("cagr"),
        "max_drawdown": perf.get("max_drawdown"),
        "sharpe": perf.get("sharpe"),
        "n_trades": perf.get("n_trades"),
        "hold_days": hold_days,
    })

print("\n\n=== 汇总 ===")
for r in results:
    print(json.dumps(r, ensure_ascii=False))
