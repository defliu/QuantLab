# coding: utf-8
"""B1 P13 trail15 引擎级验证（= 模拟盘任务书 T-20260817-001 T1）

内容: strategy/huang_529.py 加 trailing_stop 参数，n12_h60_s12 ± trail15，全样本 + 2019-2022 + 2023-2026 三段

预注册判定规则:
  trail15 相对 base:
    全样本 CAGR 改善 ≥ +0.3pp
    且 2023-2026 段 CAGR 不劣化 > 0.3pp
    且最大回撤不恶化 > 2pp
  → 部署含 trail15；否则部署纯 base

基线锚（A-B1 基线复现）:
  base 组（trail关）三段必须逐字段一致复现 v3_1overN复跑判定_20260816.md：
  全样本 总+54.9%/CAGR 6.27%/回撤-23.19%/夏普0.425/835笔
  2019-2022 +67.0%/14.22%/-21.97%/0.769/454笔
  2023-2026 +25.4%/7.00%/-19.09%/0.497/372笔
"""
import json
import os
import sys
import time

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtest.engine import run_backtest
from backtest import report
from data.astock_reader import AstockParquetReader
from data.universe import load_universe
import yaml

BASE_CONFIG = "D:/QuantLab/projects/Project_13_529主升浪/config/huang529_v2.yaml"
SIGNAL_TABLE = "D:/QuantLab/projects/Project_13_529主升浪/research/signal_table_529_top16.json"
MARKET_MA200 = "D:/QuantLab/projects/Project_13_529主升浪/research/market_ma200.json"
REPORT_ROOT = "D:/QuantLab/reports"
OUT_DIR = "D:/QuantLab/projects/Project_13_529主升浪/results"

SEGMENTS = [
    ("full", "2019-01-01", "2026-06-30"),
    ("1922", "2019-01-01", "2022-12-31"),
    ("2326", "2023-01-01", "2026-06-30"),
]

# n12_h60_s12 基线参数（与 v3_1overN 一致）
BASE_OVERRIDES = {
    "n_hold": 12,
    "max_holding_days": 60,
    "stop_loss": -0.12,
    "market_gate": 0,
    "gate_mode": "exit",
    "max_single_pct": 1.0 / 12,
    "signal_window": 1,
    "trailing_stop": None,  # base = 关
}

TRAIL15_OVERRIDES = dict(BASE_OVERRIDES, trailing_stop=0.15)

_log = []
def log(*args):
    s = " ".join(str(a) for a in args)
    print(s, flush=True)
    _log.append(s)


def _cfg_for(name, start, end, overrides):
    with open(BASE_CONFIG, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read())
    bt = dict(cfg["backtest"])
    bt["name"] = name
    bt["start_date"] = start
    bt["end_date"] = end
    strat_cfg = dict(cfg["strategy_params"])
    strat_cfg.update(overrides)
    return cfg, bt, strat_cfg


def run_one(name, start, end, overrides, results_dir):
    cfg, bt, strat_cfg = _cfg_for(name, start, end, overrides)
    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(cfg["data"]["path"],
                                 adjustment=cfg["data"].get("adjustment", "raw"))
    with open(SIGNAL_TABLE, "r", encoding="utf-8") as f:
        signal_table = json.load(f)
    with open(MARKET_MA200, "r", encoding="utf-8") as f:
        market_ma200 = json.load(f)

    report.set_results_dir(results_dir)
    try:
        result = run_backtest(
            reader=reader,
            universe=universe,
            start_date=bt["start_date"],
            end_date=bt["end_date"],
            strategy_config=strat_cfg,
            execution_cfg=cfg["execution"],
            initial_cash=float(bt.get("initial_cash", 1000000.0)),
            aux_data={"huang_529_signals": signal_table,
                      "huang_529_market_ma200": market_ma200},
            benchmark_code=bt.get("benchmark_code"),
            benchmark_db_path=bt.get("benchmark_db_path"),
            config_name=name,
            strategy_name="huang_529",
            trading_model="next_open",
        )
    finally:
        reader.close()

    rd = report.write_all(result, config_name=name)
    p = result["summary"]["performance"]
    log("[%s] dir=%s" % (name, rd))
    log("  total=%7.2f%%  cagr=%6.2f%%(linear=%6.2f%%)  mdd=%7.2f%%  sharpe=%.3f  cagr_calmar=%.3f  trades=%d"
        % (100 * p["total_return"], 100 * p.get("cagr", p["annual_return"]),
           100 * p["annual_return"], 100 * p["max_drawdown"], p["sharpe"],
           p.get("cagr_calmar") or 0, p["n_trades"]))
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
    log("======== B1 P13 trail15 引擎级验证 ========")
    log("运行时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))

    results = {}

    # 1. Base 组（trailing_stop=None）
    for seg, start, end in SEGMENTS:
        name = "B1_base_%s" % seg
        log("\n--- %s ---" % name)
        try:
            row = run_one(name, start, end, BASE_OVERRIDES, REPORT_ROOT)
            results[("base", seg)] = row
        except Exception as e:
            import traceback
            traceback.print_exc()
            results[("base", seg)] = {"error": str(e)}

    # 2. Trail15 组
    for seg, start, end in SEGMENTS:
        name = "B1_trail15_%s" % seg
        log("\n--- %s ---" % name)
        try:
            row = run_one(name, start, end, TRAIL15_OVERRIDES, REPORT_ROOT)
            results[("trail15", seg)] = row
        except Exception as e:
            import traceback
            traceback.print_exc()
            results[("trail15", seg)] = {"error": str(e)}

    # 3. 基线锚检查
    log("\n======== 基线锚检查 (A-B1) ========")
    anchor = {
        ("base", "full"): {"total_return": 0.549, "cagr": 0.0627, "max_drawdown": -0.2319, "sharpe": 0.425, "n_trades": 835},
        ("base", "1922"): {"total_return": 0.670, "cagr": 0.1422, "max_drawdown": -0.2197, "sharpe": 0.769, "n_trades": 454},
        ("base", "2326"): {"total_return": 0.254, "cagr": 0.0700, "max_drawdown": -0.1909, "sharpe": 0.497, "n_trades": 372},
    }
    anchor_pass = True
    for key, expected in anchor.items():
        actual = results.get(key, {})
        if "error" in actual:
            log("  %s: ERROR - %s" % (key, actual["error"]))
            anchor_pass = False
            continue
        # 容差: total ±2pp, cagr ±0.5pp, drawdown ±2pp, sharpe ±0.05, trades ±5%
        checks = []
        for metric, exp_val in expected.items():
            act_val = actual.get(metric, 0)
            if metric == "n_trades":
                ok = abs(act_val - exp_val) / max(1, exp_val) < 0.05
            elif metric in ("cagr", "total_return"):
                ok = abs(act_val - exp_val) < 0.005
            elif metric == "max_drawdown":
                ok = abs(act_val - exp_val) < 0.02
            elif metric == "sharpe":
                ok = abs(act_val - exp_val) < 0.05
            else:
                ok = abs(act_val - exp_val) < 0.02
            checks.append((metric, exp_val, act_val, ok))
            if not ok:
                anchor_pass = False
        log("  %s: %s" % (key, " / ".join(
            "%s=%.4f(vs%.4f %s)" % (m, a, e, "OK" if ok else "MISMATCH")
            for m, e, a, ok in checks)))

    if not anchor_pass:
        log("!!! 基线锚不通过, 停止判定, 需排查 !!!")

    # 4. 预注册判定
    log("\n======== 预注册判定 ========")
    base_full = results.get(("base", "full"), {})
    trail_full = results.get(("trail15", "full"), {})
    base_2326 = results.get(("base", "2326"), {})
    trail_2326 = results.get(("trail15", "2326"), {})

    if anchor_pass and "error" not in base_full and "error" not in trail_full:
        cagr_improvement = (trail_full.get("cagr", 0) - base_full.get("cagr", 0)) * 100
        cagr_2326_diff = (trail_2326.get("cagr", 0) - base_2326.get("cagr", 0)) * 100
        mdd_diff = (trail_full.get("max_drawdown", 0) - base_full.get("max_drawdown", 0)) * 100  # more negative = worse

        log("全样本 CAGR 改善: %+.2fpp (需≥+0.3pp)" % cagr_improvement)
        log("2023-2026段 CAGR 变化: %+.2fpp (需不劣化>0.3pp即≥-0.3pp)" % cagr_2326_diff)
        log("最大回撤变化: %+.2fpp (需不恶化>2pp即≤+2pp)" % mdd_diff)

        c1 = cagr_improvement >= 0.3
        c2 = cagr_2326_diff >= -0.3
        c3 = mdd_diff <= 2.0  # 回撤更负=恶化, mdd_diff>2表示恶化超2pp
        # 注意：max_drawdown是负数, 恶化=更负, diff=trail-base, 若trail更负则diff为负, |恶化|=|diff|
        # 改用绝对值判断
        mdd_worsening = abs(trail_full.get("max_drawdown", 0)) - abs(base_full.get("max_drawdown", 0))
        c3 = mdd_worsening <= 2.0  # 回撤加深不超过2pp

        log("回撤恶化量: %+.2fpp (需≤2pp)" % mdd_worsening)

        all_pass = c1 and c2 and c3
        log("\n判定: %s" % ("PASS → 部署含trail15" if all_pass else "FAIL → 部署纯base"))
        log("  CAGR改善≥0.3pp: %s (%+.2fpp)" % ("PASS" if c1 else "FAIL", cagr_improvement))
        log("  近段不劣化>0.3pp: %s (%+.2fpp)" % ("PASS" if c2 else "FAIL", cagr_2326_diff))
        log("  回撤不恶化>2pp: %s (%+.2fpp)" % ("PASS" if c3 else "FAIL", mdd_worsening))
    else:
        all_pass = False
        log("基线锚未通过或数据缺失，无法判定")

    # 5. 写报告
    lines = []
    lines.append("# B1 P13 trail15 引擎级验证（2026-08-18）\n")
    lines.append("> 通宵批次任务书 T-20260817-004 / 模拟盘任务书 T-20260817-001 T1")
    lines.append("> 运行时间: %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))

    lines.append("## 一、对比表\n")
    lines.append("| 组 | 时段 | 总收益 | CAGR | 线性年化 | 回撤 | 夏普 | 卡玛 | 交易数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for variant in ("base", "trail15"):
        for seg, _, _ in SEGMENTS:
            r = results.get((variant, seg), {})
            if "error" in r:
                lines.append("| %s | %s | ERROR | | | | | | |" % (variant, seg))
                continue
            lines.append("| %s | %s | %+.1f%% | %.2f%% | %.2f%% | %.2f%% | %.3f | %.3f | %d |" % (
                variant, seg,
                r.get("total_return", 0) * 100,
                r.get("cagr", 0) * 100,
                r.get("annual_return", 0) * 100,
                r.get("max_drawdown", 0) * 100,
                r.get("sharpe", 0),
                r.get("cagr_calmar") or 0,
                r.get("n_trades", 0),
            ))

    lines.append("\n## 二、基线锚检查\n")
    lines.append("锚源: v3_1overN复跑判定_20260816.md")
    lines.append("结果: %s\n" % ("PASS" if anchor_pass else "FAIL"))

    lines.append("## 三、预注册判定\n")
    lines.append("规则: trail15相对base全样本CAGR改善≥+0.3pp 且 2023-2026段CAGR不劣化>0.3pp 且最大回撤不恶化>2pp")
    if anchor_pass and "error" not in base_full and "error" not in trail_full:
        lines.append("- 全样本CAGR改善: %+.2fpp" % cagr_improvement)
        lines.append("- 2023-2026段CAGR变化: %+.2fpp" % cagr_2326_diff)
        lines.append("- 回撤恶化量: %+.2fpp" % mdd_worsening)
        lines.append("\n**判定结论: %s**" % ("部署含trail15" if all_pass else "部署纯base(n12_h60_s12)"))
    else:
        lines.append("基线锚未通过，无法判定")

    lines.append("\n## 四、部署口径\n")
    if all_pass:
        lines.append("| 项 | 值 |")
        lines.append("|---|---|")
        lines.append("| n_hold | 12 |")
        lines.append("| max_single_pct | 1/12 ≈ 0.0833 |")
        lines.append("| stop_loss | -0.12 |")
        lines.append("| max_holding_days | 60 |")
        lines.append("| trailing_stop | 0.15 |")
        lines.append("| market_gate | 0 (关闭) |")
        lines.append("| signal_window | 1 |")
    else:
        lines.append("| 项 | 值 |")
        lines.append("|---|---|")
        lines.append("| n_hold | 12 |")
        lines.append("| max_single_pct | 1/12 ≈ 0.0833 |")
        lines.append("| stop_loss | -0.12 |")
        lines.append("| max_holding_days | 60 |")
        lines.append("| trailing_stop | None (关闭) |")
        lines.append("| market_gate | 0 (关闭) |")
        lines.append("| signal_window | 1 |")

    lines.append("\n## 五、执行信息\n")
    lines.append("- 总用时: %.0fs" % (time.time() - t_start))

    report_text = "\n".join(lines)
    out_path = os.path.join(OUT_DIR, "trail15引擎验证_20260818.md")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    os.replace(tmp_path, out_path)
    log("报告: %s" % out_path)

    # 也写json结果
    json_results = {}
    for (variant, seg), r in results.items():
        json_results["%s_%s" % (variant, seg)] = r
    json_path = os.path.join(OUT_DIR, "B1_trail15_scan.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_results, f, ensure_ascii=False, indent=2)

    log("\n总用时 %.0fs" % (time.time() - t_start))


if __name__ == "__main__":
    main()
