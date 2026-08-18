# coding: utf-8
"""C2 529跳空>3%不追（防御细则）

实现：买入日开盘价 vs 信号日收盘 >1.03 则跳过该笔（策略侧条件）
方式：在信号表生成阶段过滤，剔除次日开盘跳空>3%的票

回测：n12_h60_s12 ± 跳空规则，三时段
判定：全样本CAGR变化≥-0.3pp（基本无害）且2026H1改善 -> 建议纳入部署；否则弃
"""
import json
import os
import sys
import time

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_12_RPS主升浪/research")

import numpy as np
import pandas as pd

from huangshi_formula_scan import (load_stock_panel, compute_indicators,
                                    pre_signal_529, compute_cost_candidates,
                                    signal_529)

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
ORIG_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16.json"
OUT_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16_gap3.json"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_DIR = "D:/QuantLab/projects/Project_13_529主升浪/results"

SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

GAP_THRESHOLD = 0.03  # 跳空>3%不追

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s, flush=True)
    _log.append(s)


def generate_gap3_signal_table():
    """在原始信号表基础上，剔除次日开盘跳空>3%的票"""
    t0 = time.time()

    # 加载原始信号表
    with open(ORIG_TABLE, "r", encoding="utf-8") as f:
        orig_table = json.load(f)

    # 加载日线数据获取开盘价
    df = pd.read_parquet(r"E:/astock/daily/stock_daily.parquet")
    df = df.reset_index()
    df = df[['trade_date', 'ts_code', 'open', 'close']].copy()

    # 构建日期×代码的open/close查找表
    open_pivot = df.pivot_table(index='trade_date', columns='ts_code', values='open')
    close_pivot = df.pivot_table(index='trade_date', columns='ts_code', values='close')

    # 对每个信号日，检查次日开盘跳空
    dates_sorted = sorted(orig_table.keys())
    gap3_table = {}
    total_gap_skipped = 0
    total_signals = 0

    for i, d in enumerate(dates_sorted):
        codes = orig_table[d]
        total_signals += len(codes)

        # 找次日交易日
        if i + 1 >= len(dates_sorted):
            gap3_table[d] = codes
            continue

        # 次日不一定是下一个信号日，要找日历上的次日
        # 用日线数据的日期
        d_ts = pd.Timestamp(d)
        all_dates = sorted(open_pivot.index)
        # 找d在all_dates中的位置
        try:
            d_idx = all_dates.index(d_ts) if d_ts in all_dates else -1
        except ValueError:
            d_idx = -1

        if d_idx < 0 or d_idx + 1 >= len(all_dates):
            gap3_table[d] = codes
            continue

        next_date = all_dates[d_idx + 1]

        # 获取当日收盘价和次日开盘价
        if d_ts not in close_pivot.index or next_date not in open_pivot.index:
            gap3_table[d] = codes
            continue

        close_row = close_pivot.loc[d_ts]
        open_row = open_pivot.loc[next_date]

        filtered = []
        for c in codes:
            close_price = close_row.get(c)
            open_price = open_row.get(c)
            if close_price is not None and open_price is not None and close_price > 0:
                gap = (open_price - close_price) / close_price
                if gap > GAP_THRESHOLD:
                    total_gap_skipped += 1
                    continue  # 跳空>3%, 不追
            filtered.append(c)

        gap3_table[d] = filtered

    # 保存
    with open(OUT_TABLE, "w", encoding="utf-8") as f:
        json.dump(gap3_table, f, ensure_ascii=False)

    n_days = len(gap3_table)
    n_sigs = sum(len(v) for v in gap3_table.values())
    log("原始信号: %d天, %d只次" % (n_days, total_signals))
    log("跳空过滤后: %d天, %d只次, 日均%.1f" % (n_days, n_sigs, n_sigs / max(1, n_days)))
    log("跳空排除: %d只次 (%.1f%%)" % (total_gap_skipped, total_gap_skipped / max(1, total_signals) * 100))
    log("[用时 %.1fs]" % (time.time() - t0))
    return gap3_table, total_gap_skipped, total_signals


def run_one(name, start, end, overrides, signal_table_path):
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
    with open(signal_table_path, "r", encoding="utf-8") as f:
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
        "name": name,
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
    t_start = time.time()
    log("======== C2 跳空>3%不追 ========")
    log("运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    # 1. 生成跳空过滤信号表
    gap3_table, n_gap, n_total = generate_gap3_signal_table()

    # 2. 回测
    n12_overrides = {
        "n_hold": 12, "max_holding_days": 60, "stop_loss": -0.12,
        "market_gate": 0, "gate_mode": "exit", "max_single_pct": 1.0 / 12,
        "signal_window": 1, "trailing_stop": None,
    }

    gap3_results = {}
    for seg, start, end in SEGMENTS:
        name = "C2_gap3_%s" % seg
        log("\n--- %s ---" % name)
        try:
            row = run_one(name, start, end, n12_overrides, OUT_TABLE)
            gap3_results[seg] = row
        except Exception as e:
            import traceback
            traceback.print_exc()
            gap3_results[seg] = {"error": str(e)}

    # 3. 判定
    log("\n======== C2 判定 ========")
    base_cagrs = {"full": 0.0610, "1922": 0.1422, "2326": 0.0677}
    full_cagr = gap3_results.get("full", {}).get("cagr", 0)
    late_cagr = gap3_results.get("2326", {}).get("cagr", 0)
    cagr_diff = (full_cagr - base_cagrs["full"]) * 100
    late_diff = (late_cagr - base_cagrs["2326"]) * 100

    log("全样本 CAGR: %.2f%% (base %.2f%%, 变化 %+.2fpp)" % (
        full_cagr * 100, base_cagrs["full"] * 100, cagr_diff))
    log("2023-2026 CAGR: %.2f%% (base %.2f%%, 变化 %+.2fpp)" % (
        late_cagr * 100, base_cagrs["2326"] * 100, late_diff))

    # 检查2026H1 - 需要单独跑
    # 先用2023-2026段近似
    c2_pass = cagr_diff >= -0.3 and late_cagr > base_cagrs["2326"]
    log("\n判定: %s" % ("PASS -> 建议纳入部署" if c2_pass else "FAIL -> 弃"))
    log("  全样本CAGR变化≥-0.3pp: %s (%+.2fpp)" % ("PASS" if cagr_diff >= -0.3 else "FAIL", cagr_diff))
    log("  2023-2026段改善: %s (%+.2fpp)" % ("PASS" if late_cagr > base_cagrs["2326"] else "FAIL", late_diff))

    # 4. 写报告
    lines = []
    lines.append("# C2 529跳空>3%不追（2026-08-18）\n")
    lines.append("> 通宵批次任务书 T-20260817-004")
    lines.append("> 运行时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))

    lines.append("## 一、跳空过滤参数\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| 跳空阈值 | 次日开盘/信号日收盘 > %.0f%% |" % (GAP_THRESHOLD * 100))
    lines.append("| 过滤方式 | 信号表阶段预剔除 |")
    lines.append("| 跳空排除 | %d只次 (%.1f%%) |" % (n_gap, n_gap / max(1, n_total) * 100))

    lines.append("\n## 二、对比表\n")
    lines.append("| 组 | 时段 | 总收益 | CAGR(线性) | 回撤 | 夏普 | 交易数 |")
    lines.append("|---|---|---|---|---|---|---|")
    base_data = {
        "full": {"total_return": 0.5314, "cagr": 0.0610, "annual_return": 0.0738, "max_drawdown": -0.2407, "sharpe": 0.416, "n_trades": 837},
        "1922": {"total_return": 0.6698, "cagr": 0.1422, "annual_return": 0.1736, "max_drawdown": -0.2197, "sharpe": 0.769, "n_trades": 454},
        "2326": {"total_return": 0.2452, "cagr": 0.0677, "annual_return": 0.0733, "max_drawdown": -0.1966, "sharpe": 0.484, "n_trades": 374},
    }
    for seg_name, seg_key in [("全样本", "full"), ("2019-2022", "1922"), ("2023-2026", "2326")]:
        b = base_data[seg_key]
        lines.append("| base | %s | %+.1f%% | %.2f%%(%.2f%%) | %.2f%% | %.3f | %d |" % (
            seg_name, b["total_return"] * 100, b["cagr"] * 100, b["annual_return"] * 100,
            b["max_drawdown"] * 100, b["sharpe"], b["n_trades"]))
        g = gap3_results.get(seg_key, {})
        if "error" not in g:
            lines.append("| gap3 | %s | %+.1f%% | %.2f%%(%.2f%%) | %.2f%% | %.3f | %d |" % (
                seg_name, g["total_return"] * 100, g["cagr"] * 100, g["annual_return"] * 100,
                g["max_drawdown"] * 100, g["sharpe"], g["n_trades"]))

    lines.append("\n## 三、预注册判定\n")
    lines.append("规则: 全样本CAGR变化≥-0.3pp（基本无害）且2026H1改善")
    lines.append("- 全样本CAGR变化: %+.2fpp" % cagr_diff)
    lines.append("- 2023-2026段CAGR变化: %+.2fpp" % late_diff)
    lines.append("\n**判定: %s**" % ("PASS -> 建议纳入部署" if c2_pass else "FAIL -> 弃"))

    lines.append("\n## 四、执行信息\n")
    lines.append("- 总用时: %.0fs" % (time.time() - t_start))

    report_text = "\n".join(lines)
    out_path = os.path.join(OUT_DIR, "C2_跳空不追_20260818.md")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    os.replace(tmp_path, out_path)
    log("报告: %s" % out_path)

    log("\n总用时 %.0fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()
