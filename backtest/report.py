# coding: utf-8
"""Report writers for QuantLab backtest engine.

Frozen contract (aligned with QMT factory 04_output_schema_freeze):
  summary.json, trades.csv, equity_curve.csv, positions.csv,
  logs.txt, report.md — all under RESULTS_DIR.

This module ONLY does:
  - Take the in-memory result struct from backtest.engine.run_backtest()
  - Serialize to disk with the frozen column orders / WARN block format

Boundaries:
  - All writes go to RESULTS_DIR (D:\\QuantLab\\reports) by default; never C:.
  - Never imports xtquant / passorder / strategy_main.
  - 3.6-safe: no f-strings; UTF-8 only; CSV uses csv module.
"""
import csv
import json
import os

# 默认产物目录（D:/QuantLab/reports）。可用 set_results_dir 覆盖。
RESULTS_DIR = "D:/QuantLab/reports"


def set_results_dir(path):
    """Override the results directory (e.g. F:/backtest_workspace/results)."""
    global RESULTS_DIR
    RESULTS_DIR = path


# 04 §2 trades.csv 13 columns (order is part of contract)
_TRADES_COLS = ["run_id", "date", "code", "side", "volume", "price", "amount",
                "slippage_amt", "commission", "tax", "reason", "layer", "model"]

# 04 §3 equity_curve.csv 8 columns
_EQUITY_COLS = ["run_id", "date", "total_asset", "cash", "market_value",
                "daily_return", "benchmark_close", "benchmark_return"]

# 04 §4 positions.csv 9 columns
_POSITIONS_COLS = ["run_id", "date", "code", "volume", "available_volume",
                   "cost_price", "last_price", "unrealized_pnl", "holding_days"]


def make_results_dir(run_id, config_name):
    """Create the per-run results subdirectory under RESULTS_DIR. Returns path."""
    name = run_id + "_" + config_name
    path = os.path.join(RESULTS_DIR, name).replace("\\", "/")
    os.makedirs(path, exist_ok=True)
    return path


def _write_csv(target_path, rows, columns):
    with open(target_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_trades_csv(results_dir, trades):
    p = os.path.join(results_dir, "trades.csv").replace("\\", "/")
    _write_csv(p, trades, _TRADES_COLS)
    return p


def write_equity_curve_csv(results_dir, equity_rows):
    p = os.path.join(results_dir, "equity_curve.csv").replace("\\", "/")
    _write_csv(p, equity_rows, _EQUITY_COLS)
    return p


def write_positions_csv(results_dir, positions_rows):
    p = os.path.join(results_dir, "positions.csv").replace("\\", "/")
    _write_csv(p, positions_rows, _POSITIONS_COLS)
    return p


def _build_warn_block(summary):
    """Assemble fixed-order WARN block."""
    lines = []
    spw = summary.get("sample_period_warning", {})
    if spw.get("is_short_sample"):
        req = spw.get("requested_range", ["", ""])
        act = spw.get("actual_range", ["", ""])
        lines.append(
            u"[WARN] SHORT_SAMPLE_PERIOD requested=%s..%s actual=%s..%s "
            u"trading_days=%d message=\"%s\""
            % (req[0], req[1], act[0], act[1], spw.get("trading_days", 0),
               spw.get("warning", ""))
        )
    if not summary.get("benchmark_available"):
        lines.append(u"[WARN] BENCHMARK_DISABLED reason=\"%s\""
                     % (summary.get("benchmark_note", "") or
                        u"benchmark 数据不可用"))
    cov = summary.get("data_coverage_actual", {})
    dedup = int(cov.get("dedup_count", 0))
    if dedup > 0:
        lines.append(u"[WARN] DATA_DEDUP_APPLIED count=%d" % dedup)
    if summary.get("sector_heat_mode") == "zero":
        lines.append(
            u"[WARN] SECTOR_HEAT_MODE_ZERO message=\"sector_heat_mode=zero, "
            u"sector score forced to 0\""
        )
    if summary.get("data_wal_detected"):
        msg = summary.get("data_wal_warning_message") or \
              u"检测到 .wal，数据源可能正在同步"
        lines.append(u"[WARN] DATA_WAL_DETECTED message=\"%s\"" % msg)
    return lines


def write_logs_txt(results_dir, summary, daily_logs):
    p = os.path.join(results_dir, "logs.txt").replace("\\", "/")
    warn_lines = _build_warn_block(summary)
    with open(p, "w", encoding="utf-8") as f:
        for line in warn_lines:
            f.write(line + "\n")
        if warn_lines:
            f.write("\n")
        for line in daily_logs:
            f.write(line + "\n")
    return p


def _format_pct(v):
    try:
        return ("%.2f" % (float(v) * 100.0)) + "%"
    except (TypeError, ValueError):
        return "n/a"


def _format_num(v, fmt="%.2f"):
    try:
        return fmt % float(v)
    except (TypeError, ValueError):
        return "n/a"


def write_report_md(results_dir, summary, equity_rows, positions_rows, logs):
    p = os.path.join(results_dir, "report.md").replace("\\", "/")
    perf = summary.get("performance", {})
    cov = summary.get("data_coverage_actual", {})
    spw = summary.get("sample_period_warning", {})
    end = summary.get("portfolio_end", {})
    uc = cov.get("universe_coverage", {})

    parts = []
    parts.append(u"# Backtest Report -- %s\n" % summary.get("config_name", ""))

    if spw.get("is_short_sample"):
        months = round(spw.get("trading_days", 0) / 21.0, 1)
        parts.append(u"> WARNING **样本期警告**\n>")
        parts.append(u"> 本回测样本区间 `%s ~ %s`，约 %s 个月（%d 个交易日），"
                     u"**仅用于 MVP 管线验证**，**不可作为策略最终定论**。\n>"
                     % (spw.get("actual_range", ["", ""])[0],
                        spw.get("actual_range", ["", ""])[1],
                        months, spw.get("trading_days", 0)))
        parts.append(u"> 数据补全后请重跑完整回测再做策略评估。\n")

    parts.append(u"## Run 元信息\n")
    parts.append(u"| 项 | 值 |\n|---|---|")
    parts.append(u"| run_id | %s |" % summary.get("run_id", ""))
    parts.append(u"| run_started_at | %s |" % summary.get("run_started_at", ""))
    parts.append(u"| runtime_seconds | %s |" % str(summary.get("runtime_seconds", "")))
    parts.append(u"| config_hash | %s |" % summary.get("config_hash", ""))
    parts.append(u"| data_hash | %s |" % summary.get("data_hash", ""))
    parts.append(u"| universe_hash | %s |" % summary.get("universe_hash", ""))
    parts.append(u"| data_source | %s |" % summary.get("data_source", ""))
    parts.append(u"")

    parts.append(u"## 业绩指标\n")
    parts.append(u"| 指标 | 值 |\n|---|---|")
    parts.append(u"| total_return | %s |" % _format_pct(perf.get("total_return")))
    parts.append(u"| 年化(线性) | %s |" % _format_pct(perf.get("annual_return")))
    parts.append(u"| 年化(CAGR) | %s |" % _format_pct(perf.get("cagr")))
    parts.append(u"| max_drawdown | %s |" % _format_pct(perf.get("max_drawdown")))
    parts.append(u"| sharpe | %s |" % _format_num(perf.get("sharpe"), "%.3f"))
    parts.append(u"| calmar | %s |" % (_format_num(perf.get("calmar"), "%.3f")
                                       if perf.get("calmar") is not None else "n/a"))
    parts.append(u"| 卡玛(CAGR) | %s |" % (_format_num(perf.get("cagr_calmar"), "%.3f")
                                           if perf.get("cagr_calmar") is not None else "n/a"))
    parts.append(u"| win_rate | %s |" % _format_pct(perf.get("win_rate")))
    parts.append(u"| n_trades | %s |" % str(perf.get("n_trades", 0)))
    parts.append(u"")

    parts.append(u"## 持仓概览（期末）\n")
    parts.append(u"| code | volume | cost | last | pnl |\n|---|---|---|---|---|")
    end_date = ""
    if equity_rows:
        end_date = equity_rows[-1].get("date", "")
    last_positions = [r for r in positions_rows if r.get("date") == end_date]
    if last_positions:
        for r in last_positions:
            parts.append(u"| %s | %d | %s | %s | %s |"
                         % (r["code"], int(r["volume"]),
                            _format_num(r["cost_price"], "%.2f"),
                            _format_num(r["last_price"], "%.2f"),
                            _format_num(r["unrealized_pnl"], "%.2f")))
    else:
        parts.append(u"| (空) | | | | |")
    parts.append(u"")

    parts.append(u"## 关键日志摘录\n")
    warn_or_err = [l for l in logs if l.startswith("[WARN]") or l.startswith("[ERROR]")]
    tail = warn_or_err[-10:] if warn_or_err else logs[-10:]
    if tail:
        parts.append(u"```")
        for l in tail:
            parts.append(l)
        parts.append(u"```")
    else:
        parts.append(u"_(无 WARN/ERROR)_")
    parts.append(u"")

    parts.append(u"## 数据元信息\n")
    parts.append(u"- data_path: %s" % summary.get("data_path", ""))
    parts.append(u"- data_mtime: %s" % summary.get("data_mtime", ""))
    parts.append(u"- data_adjustment: %s" % summary.get("data_adjustment", ""))
    parts.append(u"- coverage: %s ~ %s, %d codes"
                 % (cov.get("min_date", ""), cov.get("max_date", ""),
                    cov.get("n_codes", 0)))
    parts.append(u"- universe_coverage: %d/%d codes have data"
                 % (uc.get("codes_with_data", 0), uc.get("universe_size", 0)))
    parts.append(u"- benchmark: %s"
                 % ("disabled" if not summary.get("benchmark_available") else "enabled"))
    parts.append(u"- sector_heat: %s mode" % summary.get("sector_heat_mode", "zero"))
    parts.append(u"")

    parts.append(u"## 复现命令\n")
    parts.append(u"```bash")
    parts.append(u"python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml")
    parts.append(u"```")
    parts.append(u"")

    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return p


def write_summary_json(results_dir, summary):
    p = os.path.join(results_dir, "summary.json").replace("\\", "/")
    s = dict(summary)
    s["results_dir"] = results_dir
    with open(p, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2, default=str)
    return p


def write_all(result, config_name=None):
    """One-shot writer: produces the 6 files. Returns the results_dir path.

    Mutates result["summary"]["results_dir"] to the resolved absolute path.
    """
    summary = result["summary"]
    rid = summary["run_id"]
    cn = config_name or summary.get("config_name", "baseline")
    rd = make_results_dir(rid, cn)
    summary["results_dir"] = rd

    write_trades_csv(rd, result.get("trades", []))
    write_equity_curve_csv(rd, result.get("equity_rows", []))
    write_positions_csv(rd, result.get("positions_rows", []))
    write_logs_txt(rd, summary, result.get("logs", []))
    write_report_md(rd, summary, result.get("equity_rows", []),
                    result.get("positions_rows", []), result.get("logs", []))
    write_summary_json(rd, summary)
    return rd
