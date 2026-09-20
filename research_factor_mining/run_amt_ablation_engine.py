# coding=utf-8
"""PX_AMT_RATIO_20D 排除条件真实约束复核（backtest/engine）

对研究口径简化版（+2.65pp）做真实约束复核：用 backtest/engine（涨停拒买/跌停拒卖/停牌拒单/
滑点/佣金/印花/整数手）跑 ATR 合格域（atr_10w_price50 口径），
基线 vs +排除"近20日均成交额/流通市值 top 20%"（用 universe_by_date PIT 快照实现，不改策略代码）。

用法：
  D:\\Python311\\python.exe research_factor_mining/run_amt_ablation_engine.py
"""
import json, os, sys, time
import numpy as np
import pandas as pd

PROJECT_ROOT = "D:/QuantLab"
sys.path.insert(0, PROJECT_ROOT)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe

DAILY_PATH = r"D:/astock/daily/stock_daily.parquet"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_DIR = HERE
START, END = "2019-01-01", "2026-09-11"

_log = []
def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    _log.append(s)


def build_universe_by_date():
    """季度快照：每个季首交易日，剔除近20日均成交额/流通市值 top 20% 股票。"""
    t0 = time.time()
    panel = pd.read_parquet(DAILY_PATH)
    df = panel.reset_index()
    df = df[(df["trade_date"] >= "2018-06-01") & (df["trade_date"] <= END)]
    dates = sorted(df["trade_date"].unique())

    # 季度首交易日
    q_period = pd.DatetimeIndex(dates).to_period("Q")
    snap_dates = []
    seen = set()
    for d, p in zip(dates, q_period):
        if p not in seen:
            seen.add(p)
            snap_dates.append(d)

    # 近20日均成交额/流通市值（amount 千元 / circ_mv 万元 -> 比例无单位依赖）
    df = df[["trade_date", "ts_code", "amount", "circ_mv"]].copy()
    df["amt"] = df["amount"].fillna(0)
    df["mv"] = df["circ_mv"].fillna(0)
    df = df[df["mv"] > 0]
    # 关键：rolling 需组内按时间升序（否则窗口错乱导致 NaN 池缩小）
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    df["amt_ma20"] = df.groupby("ts_code", sort=False)["amt"].transform(
        lambda x: x.rolling(20, min_periods=10).mean())
    df["amt_ratio"] = df["amt_ma20"] / df["mv"]

    universe_by_date = {}
    for d in snap_dates:
        day_all = df[df["trade_date"] == d]
        day = day_all.dropna(subset=["amt_ratio"])
        if len(day) < 300:
            continue
        day = day.sort_values("amt_ratio", ascending=False)
        drop_n = int(len(day) * 0.20)          # 剔除最高活跃 top 20%（降序前 20%）
        keep = day.iloc[drop_n:]["ts_code"].tolist()   # 保留其余 80%
        if len(keep) >= 100:
            universe_by_date[str(d)[:10]] = keep
    log("universe_by_date: %d snapshots, %.1fs" % (len(universe_by_date), time.time() - t0))
    return universe_by_date


def run_atr(universe_by_date, name):
    strategy_config = {
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
    execution_cfg = {
        "price": "next_open",
        "slippage": 0.001,
        "commission_rate": 0.00025,
        "tax_rate": 0.0005,
    }
    universe = load_universe("D:/QuantLab/data/universe_all_a.csv")["codes"]
    reader = AstockParquetReader(DAILY_PATH, adjustment="hfq")
    report.set_results_dir(REPORT_ROOT)
    try:
        result = run_backtest(
            reader=reader,
            universe=universe,
            start_date=START,
            end_date=END,
            strategy_config=strategy_config,
            execution_cfg=execution_cfg,
            initial_cash=100000.0,
            benchmark_code="000300.SH",
            benchmark_db_path=None,
            config_name=name,
            strategy_name="atr_lowvol",
            trading_model="next_open",
            universe_by_date=universe_by_date,
        )
    finally:
        reader.close()

    rd = report.write_all(result, config_name=name)
    p = result["summary"]["performance"]
    log("[%s] dir=%s" % (name, rd))
    log("  total=%7.2f%%  cagr=%6.2f%%  mdd=%7.2f%%  sharpe=%.3f  calmar=%.3f  trades=%d"
        % (100 * p["total_return"], 100 * p.get("cagr", p["annual_return"]),
           100 * p["max_drawdown"], p["sharpe"],
           p.get("cagr_calmar") or 0, p["n_trades"]))
    return {"name": name, "total_return": p["total_return"],
            "annual_return": p["annual_return"], "cagr": p.get("cagr", p["annual_return"]),
            "cagr_calmar": p.get("cagr_calmar"), "max_drawdown": p["max_drawdown"],
            "sharpe": p["sharpe"], "n_trades": p["n_trades"]}


def main():
    t0 = time.time()
    log("======== PX_AMT_RATIO_20D 排除条件真实约束复核 ========")
    log("运行: %s | 口径: atr_10w_price50(10万/8只/季频/真实约束)" % time.strftime("%Y-%m-%d %H:%M:%S"))

    ubd = build_universe_by_date()

    # 基线（已跑确认：16.91%/-24.80%/卡玛0.682，与 ATR 通宵报告 16.95% 吻合）不重跑，只重跑排除版
    base = {"name": "基线(无排除)", "total_return": 2.185, "annual_return": 0.0,
            "cagr": 0.1691, "cagr_calmar": 0.682, "max_drawdown": -0.2480,
            "sharpe": 0.898, "n_trades": 666}
    excl = run_atr(ubd, "amt_ablation_excl")
    results = {"基线(无排除)": base, "排除AMT_top20": excl}

    base, excl = results["基线(无排除)"], results["排除AMT_top20"]
    lines = []
    lines.append("# PX_AMT_RATIO_20D 排除条件真实约束复核\n")
    lines.append("- 生成: %s | 引擎: backtest/engine（next_open/滑点0.1%%/佣金万2.5/印花千0.5/涨停拒买/跌停拒卖/停牌拒单/整数手）" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 载体: atr_10w_price50 口径（10万/8只/季频/ATR%%<6/换手1-8%%/真实价<50/质量+动量门控/止损-8%%）")
    lines.append("- 排除: 每季首剔除「近20日均成交额/流通市值」最高 20%%（universe_by_date PIT 快照）\n")
    lines.append("| 配置 | 总收益 | CAGR | 回撤 | 夏普 | 卡玛 | 交易数 | vs基线CAGR |")
    lines.append("|---|---|---|---|---|---|---|---|")
    delta = (excl["cagr"] - base["cagr"]) * 100
    for name, r in [("基线(无排除)", base), ("排除AMT_top20", excl)]:
        lines.append("| %s | %.1f%% | %.2f%% | %.2f%% | %.3f | %.3f | %d | %+.2fpp |"
                     % (name, r["total_return"] * 100, r["cagr"] * 100,
                        r["max_drawdown"] * 100, r["sharpe"],
                        r.get("cagr_calmar") or 0, r["n_trades"],
                        delta if name.startswith("排除") else 0.0))
    lines.append("\n## 判定（对照研究口径 +2.65pp）")
    lines.append("- vs基线CAGR: %+.2fpp" % (delta * 1.0))
    lines.append("- 回撤变化: %+.2fpp" % ((excl["max_drawdown"] - base["max_drawdown"]) * 100))
    lines.append("- 卡玛变化: %+.3f" % ((excl.get("cagr_calmar") or 0) - (base.get("cagr_calmar") or 0)))
    lines.append("- 结论: %s" % ("真实约束下增益保持 -> 可进入 ATR 实盘候选评审"
                               if delta > 0.3 else "真实约束下增益不显著/消失 -> 维持研究结论不落地"))

    out = os.path.join(OUT_DIR, "amt_ablation_engine_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log("\n报告: %s | 总用时 %.0fs" % (out, time.time() - t0))


if __name__ == "__main__":
    main()
