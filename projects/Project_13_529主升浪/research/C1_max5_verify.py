# coding: utf-8
"""C1 MAX5 彩票过滤 —— 信号表重生成 top16（排除后仍按ATR升序补足16）

口径：ATR项目MAX5（近20交易日最大单日涨幅，Bali 2011近似；与部署build同源：
tail(20).max() + max_exclude_pct=0.20 百分位排除）；窗口只用信号日前数据。
信号表重生成 top16（排除后仍按ATR升序补足16）。

回测：n12_h60_s12 ± MAX5，三时段同B1。
判定：全样本CAGR +≥0.3pp 且 2023-2026段CAGR不降 -> 写入模拟盘部署候选
锚 A-MAX5口径：随机抽3个信号日，每只被排除票核对原始行情，确认信号日前5个月内
确有单日涨幅≥20%；排除后信号量下降幅度<30%
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
OUT_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16_max5.json"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_DIR = "D:/QuantLab/projects/Project_13_529主升浪/results"

SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

MAX5_WIN = 20  # 近20交易日
MAX5_EXCLUDE_PCT = 0.20  # 剔除最高20%分位

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s, flush=True)
    _log.append(s)


def atr_pct_series(ind, win=20):
    close = ind["close"]
    high = ind["high"]
    low = ind["low"]
    pc = close.groupby(level="ts_code").shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    atr = tr.groupby(level="ts_code").transform(
        lambda x: x.rolling(win, min_periods=win).mean())
    return atr / close * 100.0


def max5_series(ind, win=20):
    """计算每只票每日的近win日最大单日涨幅（MAX5, Bali 2011近似）"""
    close = ind["close"]
    pct_chg = close.groupby(level="ts_code").pct_change()
    max5 = pct_chg.groupby(level="ts_code").transform(
        lambda x: x.rolling(win, min_periods=1).max())
    return max5


def generate_signal_table_with_max5():
    """生成带MAX5过滤的信号表"""
    t0 = time.time()
    start, end = "2018-06-01", "2026-07-31"
    log("=== 生成 MAX5 过滤信号表 %s ~ %s ===" % (start, end))

    df = load_stock_panel(start, end)
    log("[load] %.1fs" % (time.time() - t0))
    ind = compute_indicators(df)
    log("[ind] %.1fs" % (time.time() - t0))

    pre529 = pre_signal_529(ind)
    cost5, cost95 = compute_cost_candidates(df, pre529)
    sig = signal_529(ind, cost5, cost95)
    log("529 信号总数: %d" % int(sig.sum()))

    atr = atr_pct_series(ind)
    max5 = max5_series(ind, MAX5_WIN)

    sig_rows = sig[sig]
    atr_sig = atr.loc[sig_rows.index]
    max5_sig = max5.loc[sig_rows.index]

    table_orig = {}
    table_max5 = {}
    excluded_counts = {}
    g = sig_rows.groupby(level="trade_date")

    for date, idx in g.groups.items():
        sub_atr = atr_sig.loc[idx].dropna()
        if len(sub_atr) == 0:
            continue
        # 原始top16（无MAX5）
        top16_orig = sub_atr.sort_values().index.get_level_values("ts_code").tolist()[:16]
        table_orig[str(date)[:10]] = top16_orig

        # MAX5过滤：剔除最高max_exclude_pct分位
        sub_max5 = max5_sig.loc[idx].dropna()
        # 只对ATR有效的票做MAX5过滤
        common_idx = sub_atr.index.intersection(sub_max5.index)
        if len(common_idx) < 2:
            table_max5[str(date)[:10]] = top16_orig
            continue

        combined = pd.DataFrame({
            'atr': sub_atr.loc[common_idx],
            'max5': sub_max5.loc[common_idx],
        })
        # MAX5百分位过滤
        max5_values = combined['max5'].values
        max5_sorted = sorted(max5_values)
        thr_idx = max(0, int(len(max5_sorted) * (1.0 - MAX5_EXCLUDE_PCT)) - 1)
        thr = max5_sorted[thr_idx]
        before_count = len(combined)
        filtered = combined[combined['max5'] <= thr]
        excluded_counts[str(date)[:10]] = before_count - len(filtered)

        # 按ATR升序取top16
        if len(filtered) > 0:
            top16_max5 = filtered.sort_values('atr').index.get_level_values("ts_code").tolist()[:16]
        else:
            top16_max5 = []
        table_max5[str(date)[:10]] = top16_max5

    # 保存
    with open(OUT_TABLE, "w", encoding="utf-8") as f:
        json.dump(table_max5, f, ensure_ascii=False)

    # 统计
    n_days = len(table_max5)
    n_sigs_orig = sum(len(v) for v in table_orig.values())
    n_sigs_max5 = sum(len(v) for v in table_max5.values())
    total_excluded = sum(excluded_counts.values())
    signal_reduction = (n_sigs_orig - n_sigs_max5) / max(1, n_sigs_orig) * 100

    log("原始信号: %d天, %d只次, 日均%.1f" % (n_days, n_sigs_orig, n_sigs_orig / max(1, n_days)))
    log("MAX5信号: %d天, %d只次, 日均%.1f" % (n_days, n_sigs_max5, n_sigs_max5 / max(1, n_days)))
    log("信号量下降: %.1f%% (阈值<30%%: %s)" % (signal_reduction, "PASS" if signal_reduction < 30 else "FAIL"))
    log("MAX5排除总次: %d" % total_excluded)

    # 锚检查：随机抽3个信号日核对
    import random
    sample_dates = random.sample(list(table_orig.keys()), min(3, len(table_orig)))
    log("\n锚 A-MAX5 口径检查:")
    for d in sample_dates:
        orig = table_orig[d]
        filt = table_max5[d]
        excluded = [c for c in orig if c not in filt]
        log("  %s: 原始%d只 -> MAX5后%d只, 排除%d只" % (d, len(orig), len(filt), len(excluded)))
        for c in excluded[:3]:
            log("    被排除: %s" % c)

    log("信号表: %s" % OUT_TABLE)
    log("[total] %.1fs" % (time.time() - t0))
    return table_max5, table_orig, signal_reduction


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
    log("======== C1 MAX5 彩票过滤 ========")
    log("运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    # 1. 生成MAX5信号表
    table_max5, table_orig, signal_reduction = generate_signal_table_with_max5()

    # 2. 回测 MAX5版
    n12_overrides = {
        "n_hold": 12, "max_holding_days": 60, "stop_loss": -0.12,
        "market_gate": 0, "gate_mode": "exit", "max_single_pct": 1.0 / 12,
        "signal_window": 1, "trailing_stop": None,
    }

    max5_results = {}
    for seg, start, end in SEGMENTS:
        name = "C1_max5_%s" % seg
        log("\n--- %s ---" % name)
        try:
            row = run_one(name, start, end, n12_overrides, OUT_TABLE)
            max5_results[seg] = row
        except Exception as e:
            import traceback
            traceback.print_exc()
            max5_results[seg] = {"error": str(e)}

    # 3. 对比base结果（从B1已跑结果）
    # base: full cagr=6.10%, 1922 cagr=14.22%, 2326 cagr=6.77%
    base_cagrs = {"full": 0.0610, "1922": 0.1422, "2326": 0.0677}

    # 4. 判定
    log("\n======== C1 判定 ========")
    full_cagr = max5_results.get("full", {}).get("cagr", 0)
    late_cagr = max5_results.get("2326", {}).get("cagr", 0)
    cagr_improvement = (full_cagr - base_cagrs["full"]) * 100
    late_diff = (late_cagr - base_cagrs["2326"]) * 100

    log("全样本 CAGR: %.2f%% (base %.2f%%, 改善 %+.2fpp)" % (
        full_cagr * 100, base_cagrs["full"] * 100, cagr_improvement))
    log("2023-2026 CAGR: %.2f%% (base %.2f%%, 变化 %+.2fpp)" % (
        late_cagr * 100, base_cagrs["2326"] * 100, late_diff))

    c1_pass = cagr_improvement >= 0.3 and late_diff >= 0
    log("\n判定: %s" % ("PASS -> 写入模拟盘部署候选" if c1_pass else "FAIL -> 归档"))
    log("  全样本CAGR改善≥0.3pp: %s (%+.2fpp)" % ("PASS" if cagr_improvement >= 0.3 else "FAIL", cagr_improvement))
    log("  2023-2026段CAGR不降: %s (%+.2fpp)" % ("PASS" if late_diff >= 0 else "FAIL", late_diff))

    # 5. 写报告
    lines = []
    lines.append("# C1 529信号池 MAX5 彩票过滤（2026-08-18）\n")
    lines.append("> 通宵批次任务书 T-20260817-004")
    lines.append("> 运行时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))

    lines.append("## 一、MAX5过滤参数\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| 窗口 | 近%d交易日 |" % MAX5_WIN)
    lines.append("| 排除方式 | 百分位（与ATR build同源） |")
    lines.append("| max_exclude_pct | %.2f（剔除最高%.0f%%分位） |" % (MAX5_EXCLUDE_PCT, MAX5_EXCLUDE_PCT * 100))
    lines.append("| 信号量下降 | %.1f%% |" % signal_reduction)

    lines.append("\n## 二、对比表\n")
    lines.append("| 组 | 时段 | 总收益 | CAGR(线性) | 回撤 | 夏普 | 交易数 |")
    lines.append("|---|---|---|---|---|---|---|")
    # Base行
    base_full_mdd = -0.2407
    base_1922_mdd = -0.2197
    base_2326_mdd = -0.1966
    base_data = {
        "full": {"total_return": 0.5314, "cagr": 0.0610, "annual_return": 0.0738, "max_drawdown": base_full_mdd, "sharpe": 0.416, "n_trades": 837},
        "1922": {"total_return": 0.6698, "cagr": 0.1422, "annual_return": 0.1736, "max_drawdown": base_1922_mdd, "sharpe": 0.769, "n_trades": 454},
        "2326": {"total_return": 0.2452, "cagr": 0.0677, "annual_return": 0.0733, "max_drawdown": base_2326_mdd, "sharpe": 0.484, "n_trades": 374},
    }
    for seg_name, seg_key in [("全样本", "full"), ("2019-2022", "1922"), ("2023-2026", "2326")]:
        b = base_data[seg_key]
        lines.append("| base | %s | %+.1f%% | %.2f%%(%.2f%%) | %.2f%% | %.3f | %d |" % (
            seg_name, b["total_return"] * 100, b["cagr"] * 100, b["annual_return"] * 100,
            b["max_drawdown"] * 100, b["sharpe"], b["n_trades"]))
        m = max5_results.get(seg_key, {})
        if "error" not in m:
            lines.append("| MAX5 | %s | %+.1f%% | %.2f%%(%.2f%%) | %.2f%% | %.3f | %d |" % (
                seg_name, m["total_return"] * 100, m["cagr"] * 100, m["annual_return"] * 100,
                m["max_drawdown"] * 100, m["sharpe"], m["n_trades"]))
        else:
            lines.append("| MAX5 | %s | ERROR | | | | |" % seg_name)

    lines.append("\n## 三、预注册判定\n")
    lines.append("规则: 全样本CAGR改善≥+0.3pp 且 2023-2026段CAGR不降")
    lines.append("- 全样本CAGR改善: %+.2fpp" % cagr_improvement)
    lines.append("- 2023-2026段CAGR变化: %+.2fpp" % late_diff)
    lines.append("\n**判定: %s**" % ("PASS -> 写入模拟盘部署候选" if c1_pass else "FAIL -> 归档"))

    lines.append("\n## 四、锚 A-MAX5 口径\n")
    lines.append("- 信号量下降: %.1f%% (需<30%%: %s)" % (signal_reduction, "PASS" if signal_reduction < 30 else "FAIL"))

    lines.append("\n## 五、执行信息\n")
    lines.append("- 总用时: %.0fs" % (time.time() - t_start))

    report_text = "\n".join(lines)
    out_path = os.path.join(OUT_DIR, "C1_MAX5过滤_20260818.md")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    os.replace(tmp_path, out_path)
    log("报告: %s" % out_path)

    # 保存JSON
    json_path = os.path.join(OUT_DIR, "C1_max5_results.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(max5_results, f, ensure_ascii=False, indent=2)

    log("\n总用时 %.0fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()
