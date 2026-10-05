# coding: utf-8
"""P13 黄氏529 · R3 信号衰减 30 交易日复评（T-20260815-007，2026-10-05）

预注册依据（防挑参数，全部跑前已有）：
- results/v3_1overN复跑判定_20260816.md §四：R3 = 维持 DOING + 信号衰减监控，30 交易日复评；
- specs/模拟盘落地任务书_20260817.md §〇.1：核心指标 = 滚动「到期均收」
  （2023-2026 基线 +4.4%，回到 +8% 以上才值得重新评估策略）；
- 模拟盘未上载（2026-09-27 诚哥拍板：修好但暂不上载，零自动化），故本复评走离线口径。

离线复评三段：
  A) 冻结公式重生成信号表至 2026-09-30（新文件落盘，8-16 冻结版 signal_table_529_top16.json
     一字不动）+ 与冻结表重叠日一致性校验（top12 必须逐只一致，T2 验收标准）；
  B) n12_h60_s12（1/n 修正最佳配置，参数冻结）引擎级复演 2019-01-01 ~ 2026-09-30，
     信号表用 A 的新表；
  C) 分年度退出结构（到期均收/止损均亏/止损占比，对齐 20260817 分析 §2.2 基线）
     + 滚动到期均收（核心指标 vs +8% 阈值）+ 复评窗口（2026-08-18 ~ 2026-09-30）
     信号流与 fwd20（同代码算全历史分年基准，防口径错位）+ 预注册判定。

用法：
  python research/r3_reeval_20261005.py --stage a
  python research/r3_reeval_20261005.py --stage b
  python research/r3_reeval_20260930.py --stage c   （打错名也能跑：stage c 只读产物）
产物：
  research/signal_table_529_top16_20261005.json / market_ma200_20261005.json
  reports/<runid>_r3reeval20261005_n12/  （引擎标准产物）
  results/r3_reeval_20261005.json（机器可读结论）
"""
import argparse
import glob
import json
import os
import sys
import time

PROJECT_ROOT = "D:/QuantLab"
PROJ = "D:/QuantLab/projects/Project_13_529主升浪"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

NEW_TABLE = os.path.join(PROJ, "research", "signal_table_529_top16_20261005.json")
NEW_MA200 = os.path.join(PROJ, "research", "market_ma200_20261005.json")
FROZEN_TABLE = os.path.join(PROJ, "research", "signal_table_529_top16.json")
ENGINE_DIR_MARKER = os.path.join(PROJ, "results", "r3reeval_20261005_enginedir.json")
OUT_JSON = os.path.join(PROJ, "results", "r3_reeval_20261005.json")
REPORT_ROOT = "D:/QuantLab/reports"

END_DATE = "2026-09-30"
WINDOW_START = "2026-08-18"   # 8-16 预注册后的首个交易周
WINDOW_END = "2026-09-30"

# 20260817 策略分析 §2.2 基线（n12 全样本，引擎 trades 口径）：到期均收%/止损均亏%/止损占比%
BASELINE_DECAY = {
    "2019": (9.9, -13.9, 30.6), "2020": (18.5, -14.6, 41.7),
    "2021": (10.0, -13.3, 32.1), "2022": (12.2, -13.5, 32.1),
    "2023": (6.6, -12.9, 18.9), "2024": (3.8, -14.6, 27.1),
    "2025": (5.6, -12.8, 22.4), "2026H1": (1.6, -14.1, 40.0),
}
THRESHOLD_REEVAL = 8.0     # 预注册：滚动到期均收回到 +8% 以上才值得重新评估
BASELINE_2326 = 4.4        # 2023-2026 到期均收基线
FWD_BENCHMARK_2024 = (3.39, 59.9)  # 公式精度报告 2024 fwd20 均值%/命中率%（外部旁证）


def stage_a():
    """A) 冻结公式重生成信号表至 9-30 + 与冻结表一致性校验。"""
    import numpy as np
    import pandas as pd
    # import 复用 gen_529_signal_table 的 atr_pct_series（保证逐字段同口径）；
    # 其模块级 sys.path 已含 Project_12 research（huangshi_formula_scan）。
    from gen_529_signal_table import atr_pct_series
    from huangshi_formula_scan import (load_stock_panel, compute_indicators,
                                       pre_signal_529, compute_cost_candidates,
                                       signal_529)

    start = "2018-06-01"
    print("=== [A] 生成 529 信号表 %s ~ %s（冻结公式）===" % (start, END_DATE), flush=True)
    t0 = time.time()
    df = load_stock_panel(start, END_DATE)
    print("[load] %.1fs" % (time.time() - t0), flush=True)
    ind = compute_indicators(df)
    print("[ind] %.1fs" % (time.time() - t0), flush=True)
    pre529 = pre_signal_529(ind)
    cost5, cost95 = compute_cost_candidates(df, pre529)
    sig = signal_529(ind, cost5, cost95)
    print("529 信号总数: %d  [%.1fs]" % (int(sig.sum()), time.time() - t0), flush=True)
    atr = atr_pct_series(ind)

    sig_rows = sig[sig]
    atr_sig = atr.loc[sig_rows.index]
    table = {}
    for date, idx in sig_rows.groupby(level="trade_date").groups.items():
        sub = atr_sig.loc[idx].dropna()
        if len(sub) == 0:
            continue
        top16 = sub.sort_values().index.get_level_values("ts_code").tolist()[:16]
        table[str(date)[:10]] = top16
    with open(NEW_TABLE, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False)
    n_days, n_sigs = len(table), sum(len(v) for v in table.values())
    print("新信号表: %d 天, %d 只次, 日均 %.1f -> %s" % (n_days, n_sigs, n_sigs / max(1, n_days), NEW_TABLE), flush=True)

    close_wide = ind["close"].unstack("ts_code")
    daily_mean = close_wide.mean(axis=1, skipna=True)
    ma200 = daily_mean.rolling(200, min_periods=200).mean()
    market_ok = {}
    for d, v in daily_mean.items():
        m = ma200.get(d)
        market_ok[str(d)[:10]] = bool(v > m) if m == m else True
    with open(NEW_MA200, "w", encoding="utf-8") as f:
        json.dump(market_ok, f, ensure_ascii=False)
    print("市场MA200表: %d 天 -> %s" % (len(market_ok), NEW_MA200), flush=True)

    # 一致性校验（重叠日；T2 验收标准：前 12 只必须逐只一致）
    with open(FROZEN_TABLE, "r", encoding="utf-8") as f:
        frozen = json.load(f)
    common = sorted(set(frozen) & set(table))
    n12m = sum(1 for d in common if frozen[d][:12] == table[d][:12])
    n16m = sum(1 for d in common if frozen[d][:16] == table[d][:16])
    mism = [d for d in common if frozen[d][:12] != table[d][:12]]
    print("[校验] 重叠 %d 天: top12 一致 %d (%.1f%%), top16 全等 %d (%.1f%%)"
          % (len(common), n12m, 100.0 * n12m / max(1, len(common)),
             n16m, 100.0 * n16m / max(1, len(common))), flush=True)
    for d in mism[:3]:
        print("  不一致例 %s: 冻结前12=%s 新前12=%s" % (d, frozen[d][:12], table[d][:12]), flush=True)
    if common:
        print("  重叠区间: %s ~ %s" % (common[0], common[-1]), flush=True)
    verdict = "PASS" if len(common) > 0 and n12m == len(common) else ("WARN" if len(common) > 0 else "NO_OVERLAP")
    print("[A 校验结论] %s（top12 全一致=数据与公式无漂移；WARN 时禁止采信 B/C）" % verdict, flush=True)
    return {"n_common": len(common), "n_top12_match": n12m,
            "n_top16_match": n16m, "consistency": verdict,
            "new_days": n_days, "new_sigs": n_sigs}


def stage_b():
    """B) n12_h60_s12 引擎级复演 2019-01-01 ~ 2026-09-30（1/n 修正最佳，参数冻结）。"""
    import yaml
    from backtest.engine import run_backtest
    from backtest import report
    from data.astock_reader import AstockParquetReader
    from data.universe import load_universe

    if not os.path.exists(NEW_TABLE):
        raise SystemExit("新信号表不存在，先跑 --stage a")
    base_cfg = os.path.join(PROJ, "config", "huang529_v2.yaml")
    with open(base_cfg, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f.read())
    bt = dict(cfg["backtest"])
    bt["start_date"], bt["end_date"] = "2019-01-01", END_DATE
    strat_cfg = dict(cfg["strategy_params"])
    strat_cfg.update({"n_hold": 12, "max_holding_days": 60, "stop_loss": -0.12,
                      "market_gate": 0, "gate_mode": "exit", "max_single_pct": 1.0 / 12})
    with open(NEW_TABLE, "r", encoding="utf-8") as f:
        signal_table = json.load(f)
    with open(NEW_MA200, "r", encoding="utf-8") as f:
        market_ma200 = json.load(f)
    universe = load_universe(os.path.abspath(cfg["universe"]["csv"]))["codes"]
    reader = AstockParquetReader(cfg["data"]["path"], adjustment=cfg["data"].get("adjustment", "raw"))

    print("=== [B] 引擎复演 n12_h60_s12 2019-01-01 ~ %s（n=%d 信号天）===" % (END_DATE, len(signal_table)), flush=True)
    t0 = time.time()
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
            config_name="r3reeval20261005_n12", strategy_name="huang_529",
            trading_model="next_open")
    finally:
        reader.close()
    rd = report.write_all(result, config_name="r3reeval20261005_n12")
    perf = result["summary"]["performance"]
    print("引擎产物: %s  [%.1fs]" % (rd, time.time() - t0), flush=True)
    print("  total=%7.2f%%  cagr=%6.2f%%  mdd=%7.2f%%  sharpe=%.3f  trades=%d"
          % (100 * perf["total_return"], 100 * perf.get("cagr", perf["annual_return"]),
             100 * perf["max_drawdown"], perf["sharpe"], perf["n_trades"]), flush=True)
    with open(ENGINE_DIR_MARKER, "w", encoding="utf-8") as f:
        json.dump({"dir": rd, "trades": int(perf["n_trades"])}, f)
    return {"dir": rd}


def _load_round_trips(trades_csv):
    """trades.csv -> 配对回合（code 顺序配对：buy 后首笔 sell）。"""
    import pandas as pd
    trades = pd.read_csv(trades_csv)
    open_buy = {}
    rounds = []
    for r in trades.itertuples():
        if r.side == "buy":
            open_buy[r.code] = r
        else:
            b = open_buy.pop(r.code, None)
            if b is None:
                continue
            rounds.append({"code": r.code, "entry": str(b.date), "exit": str(r.date),
                           "reason": r.reason, "ret": r.price / b.price - 1.0})
    return rounds


def stage_c():
    """C) 衰减表更新 + 滚动到期均收 + 复评窗口指标 + 预注册判定。"""
    import numpy as np
    import pandas as pd

    if not os.path.exists(ENGINE_DIR_MARKER):
        raise SystemExit("引擎产物 marker 不存在，先跑 --stage b")
    with open(ENGINE_DIR_MARKER, "r", encoding="utf-8") as f:
        engine_dir = json.load(f)["dir"]
    trades_csv = os.path.join(engine_dir, "trades.csv")
    rounds = _load_round_trips(trades_csv)
    rdf = pd.DataFrame(rounds)
    print("=== [C] 引擎回合: %d 笔平仓（%s ~ %s）===" % (len(rdf), rdf["exit"].min(), rdf["exit"].max()), flush=True)

    # ---- C1 分年度退出结构（对齐 §2.2 基线口径：毛价收益）----
    rdf["exit_year"] = rdf["exit"].str[:4]
    decay = {}
    print("\n-- 分年度退出结构（新复演 vs §2.2 基线）--")
    print("%-6s %-8s %8s %8s %8s   %s" % ("年份", "退出原因", "笔数", "均收%", "胜率%", "基线(均收/占比)"))
    for y in sorted(rdf["exit_year"].unique()):
        sub_y = rdf[rdf["exit_year"] == y]
        exp = sub_y[sub_y["reason"] == "holding_expired"]
        stp = sub_y[sub_y["reason"] == "stop_loss"]
        e_mean = 100.0 * exp["ret"].mean() if len(exp) else float("nan")
        s_mean = 100.0 * stp["ret"].mean() if len(stp) else float("nan")
        s_share = 100.0 * len(stp) / len(sub_y) if len(sub_y) else float("nan")
        decay[y] = {"n": len(sub_y), "n_expired": len(exp), "n_stop": len(stp),
                    "expired_mean_pct": round(e_mean, 2) if e_mean == e_mean else None,
                    "stop_mean_pct": round(s_mean, 2) if s_mean == s_mean else None,
                    "stop_share_pct": round(s_share, 1) if s_share == s_share else None,
                    "expired_win_pct": round(100.0 * (exp["ret"] > 0).mean(), 1) if len(exp) else None}
        base = BASELINE_DECAY.get(y, BASELINE_DECAY.get(y + "H1", (None, None, None)))
        print("%-6s %-8s %8d %+8.2f %8.1f   基线 %s%% / %s%%"
              % (y, "到期", len(exp), e_mean, 100.0 * (exp["ret"] > 0).mean() if len(exp) else float("nan"),
                 base[0], base[2]))
        print("%-6s %-8s %8d %+8.2f %8s   基线 %s%%"
              % (y, "止损", len(stp), s_mean, "-", base[1]))
    seg2326 = rdf[(rdf["exit"] >= "2023-01-01") & (rdf["exit"] <= "2026-12-31")]
    exp2326 = seg2326[seg2326["reason"] == "holding_expired"]
    mean_2326 = 100.0 * exp2326["ret"].mean()
    print("2023-%s 到期均收（更新口径）: %+.2f%%（基线 +4.4%%）" % (END_DATE[:4], mean_2326), flush=True)

    # ---- C2 滚动到期均收（核心指标，预注册阈值 +8%）----
    exp_all = rdf[rdf["reason"] == "holding_expired"].copy()
    rolling = {}
    for label, since in [("2026YTD", "2026-01-01"), ("近6个月", "2026-04-01"), ("近3个月", "2026-07-01")]:
        s = exp_all[exp_all["exit"] >= since]
        rolling[label] = {"n": len(s), "mean_pct": round(100.0 * s["ret"].mean(), 2),
                          "win_pct": round(100.0 * (s["ret"] > 0).mean(), 1)} if len(s) else {"n": 0}
        print("滚动到期均收[%s](exit>=%s): n=%d %+.2f%% 胜率%.1f%%"
              % (label, since, len(s), 100.0 * s["ret"].mean() if len(s) else float("nan"),
                 100.0 * (s["ret"] > 0).mean() if len(s) else float("nan")), flush=True)
    core = rolling["2026YTD"]

    # ---- C3 无漂移验证：与 8-16 旧引擎产物在重叠期（exit<=2026-06-30）逐口径对比 ----
    drift = {"note": "未找到旧 v3_1n_n12 产物"}
    old_dirs = sorted(glob.glob(os.path.join(REPORT_ROOT, "*_v3_1n_n12_h60_s12")))
    if old_dirs:
        old_csv = os.path.join(old_dirs[-1], "trades.csv")
        if os.path.exists(old_csv):
            old = pd.DataFrame(_load_round_trips(old_csv))
            o = old[old["exit"] <= "2026-06-30"]
            n_ = rdf[rdf["exit"] <= "2026-06-30"]
            same = (len(o) == len(n_)
                    and abs(100.0 * o["ret"].mean() - 100.0 * n_["ret"].mean()) < 0.05
                    and int((o["reason"] == "stop_loss").sum()) == int((n_["reason"] == "stop_loss").sum()))
            drift = {"old_dir": old_dirs[-1], "old_n": len(o), "new_n": len(n_),
                     "old_exp_mean": round(100.0 * o[o["reason"] == "holding_expired"]["ret"].mean(), 3),
                     "new_exp_mean": round(100.0 * n_[n_["reason"] == "holding_expired"]["ret"].mean(), 3),
                     "identical": bool(same)}
            print("[无漂移验证] 重叠期旧 %d 笔 vs 新 %d 笔, 到期均收 旧%.3f%%/新%.3f%% -> %s"
                  % (len(o), len(n_), drift["old_exp_mean"], drift["new_exp_mean"],
                     "一致" if same else "不一致(需排查)"), flush=True)

    # ---- C4 复评窗口信号流 + fwd20（全历史分年基准，同代码算，防口径错位）----
    with open(NEW_TABLE, "r", encoding="utf-8") as f:
        table = json.load(f)
    px = pd.read_parquet("D:/astock/daily/stock_daily.parquet", columns=["open", "close", "adj_factor"])
    idx0 = px.index.get_level_values(0)
    px = px.loc[idx0 >= pd.Timestamp("2018-06-01")]
    open_h = (px["open"] * px["adj_factor"]).unstack("ts_code")
    close_h = (px["close"] * px["adj_factor"]).unstack("ts_code")
    cal = open_h.index
    pos = {d: i for i, d in enumerate(cal)}

    def fwd20(ds, code, horizon=20):
        i = pos.get(pd.Timestamp(ds))
        if i is None or i + 1 + horizon >= len(cal):
            return None
        entry_d, exit_d = cal[i + 1], cal[i + 1 + horizon]
        if code not in open_h.columns:
            return None
        o = open_h.at[entry_d, code]
        c = close_h.at[exit_d, code]
        if not (o == o and c == c) or o <= 0:
            return None
        return c / o - 1.0

    sig_by_year = {}
    all_days = [str(d)[:10] for d in cal if str(d)[:10] >= "2019-01-01"]
    for ds in all_days:
        y = ds[:4]
        for rank, code in enumerate(table.get(ds, [])):
            r = fwd20(ds, code)
            if r is not None:
                sig_by_year.setdefault(y, []).append((ds, code, rank, r))
    print("\n-- 信号级 fwd20（T+1 开盘入，+20 交易日收盘出，全表口径）--")
    print("%-6s %8s %8s %8s" % ("信号年", "n", "均值%", "命中率%"))
    fwd_by_year = {}
    for y in sorted(sig_by_year):
        rets = [x[3] for x in sig_by_year[y]]
        fwd_by_year[y] = {"n": len(rets), "mean_pct": round(100.0 * float(np.mean(rets)), 2),
                          "hit_pct": round(100.0 * float(np.mean([r > 0 for r in rets])), 1)}
        print("%-6s %8d %+8.2f %8.1f" % (y, len(rets), 100.0 * float(np.mean(rets)),
                                         100.0 * float(np.mean([r > 0 for r in rets]))))
    print("（外部旁证：公式精度报告 2024 fwd20 %+.2f%%/命中 %.1f%%）" % FWD_BENCHMARK_2024, flush=True)

    win_days = [d for d in all_days if WINDOW_START <= d <= WINDOW_END]
    win_counts = [len(table.get(d, [])) for d in win_days]
    win_empty = sum(1 for c in win_counts if c == 0)
    win_sigs = [(d, c, rk) for d in win_days for rk, c in enumerate(table.get(d, []))]
    win_fwd = [fwd20(d, c) for d, c, rk in win_sigs]
    win_fwd_ok = [r for r in win_fwd if r is not None]
    n_all_days = len(all_days)
    n_table_days = sum(1 for d in all_days if table.get(d))
    base_daily = sum(len(table.get(d, [])) for d in all_days) / max(1, n_table_days)
    window_flow = {
        "trading_days": len(win_days), "days_with_signals": len(win_days) - win_empty,
        "signals": len(win_sigs), "mean_per_day": round(float(np.mean(win_counts)), 1) if win_days else None,
        "baseline_mean_per_day": round(base_daily, 1),
        "fwd20_evaluable_n": len(win_fwd_ok),
        "fwd20_pending_n": len(win_sigs) - len(win_fwd_ok),
        "fwd20_mean_pct": round(100.0 * float(np.mean(win_fwd_ok)), 2) if win_fwd_ok else None,
        "fwd20_hit_pct": round(100.0 * float(np.mean([r > 0 for r in win_fwd_ok])), 1) if win_fwd_ok else None,
    }
    print("\n-- 复评窗口 %s ~ %s --" % (WINDOW_START, WINDOW_END))
    print("交易日 %d（有信号 %d）, 信号 %d 只次, 日均 %.1f（全期基准 %.1f）"
          % (window_flow["trading_days"], window_flow["days_with_signals"], window_flow["signals"],
             window_flow["mean_per_day"], window_flow["baseline_mean_per_day"]))
    print("fwd20 可评估 %d 只: 均值 %s%%, 命中率 %s%%（其余 %d 只窗口未走完，仅计流量）"
          % (window_flow["fwd20_evaluable_n"], window_flow["fwd20_mean_pct"],
             window_flow["fwd20_hit_pct"], window_flow["fwd20_pending_n"]), flush=True)

    # ---- C5 预注册判定 ----
    core_mean = core.get("mean_pct")
    if core_mean is None or core.get("n", 0) < 20:
        verdict = "样本不足"
        verdict_detail = "2026YTD 到期笔数 %s(<20)，不足以按预注册阈值判定" % core.get("n")
    elif core_mean >= THRESHOLD_REEVAL:
        verdict = "触发重新评估（>= +8%）"
        verdict_detail = "滚动到期均收 %+.2f%% >= +8%%，值得重新评估策略（R2 通道）" % core_mean
    else:
        verdict = "维持 R3（未回 +8%）"
        verdict_detail = ("滚动到期均收 %+.2f%% < +8%%（基线 +4.4%%），信号延续性衰减未修复，"
                          "维持 DOING+监控、不配资、不上载" % core_mean)
    print("\n=== 预注册判定（核心指标：滚动到期均收，阈值 +8%）===")
    print("2026YTD: %s -> %s" % (core, verdict))
    print(verdict_detail, flush=True)

    out = {
        "date": "2026-10-05", "window": [WINDOW_START, WINDOW_END],
        "engine_dir": engine_dir, "decay_by_year": decay,
        "expired_mean_2326_updated": round(mean_2326, 2),
        "rolling_expired": rolling, "drift_check": drift,
        "fwd20_by_year": fwd_by_year, "window_flow": window_flow,
        "verdict": verdict, "verdict_detail": verdict_detail,
        "thresholds": {"reeval_pct": THRESHOLD_REEVAL, "baseline_2326_pct": BASELINE_2326},
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("结论落盘: %s" % OUT_JSON, flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["a", "b", "c", "all"], default="all")
    args = ap.parse_args()
    if args.stage in ("a", "all"):
        stage_a()
    if args.stage in ("b", "all"):
        stage_b()
    if args.stage in ("c", "all"):
        stage_c()


if __name__ == "__main__":
    main()
