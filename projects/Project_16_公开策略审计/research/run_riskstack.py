# coding: utf-8
"""T-20260820-001 菜场大妈六步选股 × 风控栈消融跑批入口。

用法:
  python research/run_riskstack.py --build-aux          # 首次: 构建+缓存 aux
  python research/run_riskstack.py                      # 用缓存的 aux 跑全部变体
  python research/run_riskstack.py --only R0_base_repro,R1_adv1pct

设计:
  - aux 构建逻辑与 run_a4_engine.py 逐行同款（mv/div_ttm/yoy_pit/list_date），
    新增 mkt_ew_close（全市场等权 raw close，P10 方向5 门控口径）。
    缓存 results/riskstack_aux_cache.pkl，保证各变体输入完全一致。
  - R0_base_repro 用原策略 cai_market_audit 复跑，KPI 必须与 taskb_A4_kpi.json
    一致（total_return=9.031255 等），作为管线回归闸。
  - 每个变体落盘 equity_curve.csv 到 results/riskstack_out/<runid>_<tag>/，
    汇总表追加写入 results/riskstack_summary.csv。
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, r"D:\QuantLab")

RESULTS = os.path.join(PROJ, "results")
OUT_DIR = os.path.join(RESULTS, "riskstack_out")
AUX_CACHE = os.path.join(RESULTS, "riskstack_aux_cache.pkl")
SUMMARY_CSV = os.path.join(RESULTS, "riskstack_summary.csv")
BASE = r"E:/astock"
CFG_YAML = os.path.join(PROJ, "config", "cai_market_a4.yaml")

# 注册 Project_16 内两个策略（薄入口负责，不改全局 strategy/ 目录）
import importlib.util
for _name in ("cai_market_audit", "cai_market_riskstack"):
    _path = os.path.join(PROJ, "strategy", "%s.py" % _name)
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)


def build_aux():
    """与 run_a4_engine.build_aux 逐行同款 + mkt_ew_close。"""
    t0 = time.time()
    sd = pd.read_parquet(
        f"{BASE}/daily/stock_daily.parquet",
        columns=["trade_date", "ts_code", "close", "total_mv"])
    if isinstance(sd.index, pd.MultiIndex):
        sd = sd.reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd = sd[(sd["trade_date"] >= "2017-12-31")].copy()

    # 0) 全市场等权收盘（raw close 同日截面均值；门控用，无前视）
    ew = sd.groupby("trade_date")["close"].mean().sort_index()
    mkt_ew_close = pd.Series(ew.values,
                             index=ew.index.strftime("%Y-%m-%d")).sort_index()

    # 1) total_mv 宽表
    mv_wide = sd.pivot_table(index="trade_date", columns="ts_code", values="total_mv")
    mv_wide.index = pd.DatetimeIndex(mv_wide.index).strftime("%Y-%m-%d")

    # 2) 自建 TTM 股息率
    dv = pd.read_parquet(f"{BASE}/finance/dividend.parquet")
    dv = dv[dv["div_proc"].astype(str).str.strip() == "实施"].copy()
    dv = dv[dv["ex_date"].notna() & (dv["cash_div_tax"].notna()) & (dv["cash_div_tax"] > 0)].copy()
    dv["ex_date"] = pd.to_datetime(dv["ex_date"])
    dv["ts_code"] = dv["ts_code"].astype(str)
    div_wide = dv.pivot_table(index="ex_date", columns="ts_code",
                              values="cash_div_tax", aggfunc="sum").sort_index().fillna(0.0)
    cum = div_wide.cumsum()
    dates = pd.DatetimeIndex(sd["trade_date"].unique()).as_unit("ns")
    cum_now = cum.reindex(dates).ffill().fillna(0.0)
    cum_365 = cum.reindex(dates - pd.Timedelta(days=365)).ffill().fillna(0.0)
    cum_365.index = dates
    ttm = cum_now - cum_365
    close_wide = sd.pivot_table(index="trade_date", columns="ts_code", values="close")
    close_wide.index = pd.DatetimeIndex(close_wide.index).as_unit("ns")
    dy = ttm / close_wide.replace(0, np.nan)
    dy.index = pd.DatetimeIndex(dy.index).strftime("%Y-%m-%d")
    dy = dy.reindex(sorted(dy.index))

    # 3) PIT 净利同比
    inc = pd.read_parquet(f"{BASE}/finance/income.parquet",
                          columns=["ts_code", "end_date", "f_ann_date", "ann_date", "n_income_attr_p"])
    inc["ts_code"] = inc["ts_code"].astype(str)
    inc["end_date"] = inc["end_date"].astype(str).str[:8]
    inc["f_ann_date"] = inc["f_ann_date"].astype(str).str[:8]
    inc["ann_date"] = inc["ann_date"].astype(str).str[:8]
    inc = inc[inc["end_date"].str.fullmatch(r"\d{8}")].copy()
    inc["visible"] = inc["f_ann_date"].where(
        inc["f_ann_date"].ne("nan") & inc["f_ann_date"].ne("NaT"), inc["ann_date"])
    inc = inc[inc["visible"].str.fullmatch(r"\d{8}", na=False)].copy()
    inc = inc[inc["n_income_attr_p"].notna()].copy()

    date_int = pd.DatetimeIndex(sd["trade_date"].unique()).strftime("%Y%m%d").astype(np.int64)
    date_str = pd.DatetimeIndex(sd["trade_date"].unique()).strftime("%Y-%m-%d")
    yoy_df = pd.DataFrame(index=date_str, columns=sorted(mv_wide.columns), dtype=float)
    for code, g in inc.groupby("ts_code"):
        g = g.drop_duplicates("end_date", keep="last").sort_values("end_date")
        end_int = g["end_date"].astype(np.int64).to_numpy()
        vis = g["visible"].astype(np.int64).to_numpy()
        ninc = g["n_income_attr_p"].to_numpy()
        order = np.argsort(vis)
        vis_s, end_s, ninc_s = vis[order], end_int[order], ninc[order]
        pos = np.searchsorted(vis_s, date_int, side="right") - 1
        valid = pos >= 0
        n_cur = np.where(valid, ninc_s[np.maximum(pos, 0)], np.nan)
        end_cur = np.where(valid, end_s[np.maximum(pos, 0)], 0)
        prev_end = np.where(end_cur >= 19910101, end_cur - 10000, 0)
        prev_map = dict(zip(end_int, ninc))
        n_prev = np.array([prev_map.get(int(e), np.nan) for e in prev_end], dtype=float)
        yoy = np.where(np.isfinite(n_cur) & np.isfinite(n_prev) & (n_prev != 0),
                       n_cur / np.where(n_prev == 0, np.nan, n_prev) - 1.0, np.nan)
        if code in yoy_df.columns:
            yoy_df[code] = yoy

    # 4) list_date
    sb = pd.read_parquet(f"{BASE}/basic/stock_basic.parquet")
    sb["ts_code"] = sb["ts_code"].astype(str)
    sb["list_date"] = pd.to_datetime(sb["list_date"])
    list_date = {r.ts_code: r.list_date
                 for r in sb.itertuples() if pd.notna(r.list_date)}
    print(f"[aux] mv_wide={mv_wide.shape} dy={dy.shape} yoy={yoy_df.shape} "
          f"list_date={len(list_date)} mkt_ew={len(mkt_ew_close)} ({time.time()-t0:.1f}s)", flush=True)
    return {"total_mv": mv_wide, "div_ttm": dy, "yoy_pit": yoy_df,
            "list_date": list_date, "mkt_ew_close": mkt_ew_close}


def load_aux(rebuild=False):
    if (not rebuild) and os.path.exists(AUX_CACHE):
        t0 = time.time()
        aux = pd.read_pickle(AUX_CACHE)
        print(f"[aux] loaded cache ({time.time()-t0:.1f}s): "
              f"mv={aux['total_mv'].shape} yoy={aux['yoy_pit'].shape}", flush=True)
        return aux
    aux = build_aux()
    pd.to_pickle(aux, AUX_CACHE)
    print(f"[aux] cached -> {AUX_CACHE}", flush=True)
    return aux


# ---------------------------------------------------------------------------
# 变体矩阵：(tag, strategy_name, strategy_params 覆盖, execution 覆盖)
# ---------------------------------------------------------------------------
S_BASE = "cai_market_audit"
S_RISK = "cai_market_riskstack"
VARIANTS = [
    # --- 锚复现（管线回归闸：必须逐位对上 taskb_A4_kpi.json）---
    ("R0_base_repro", S_BASE, {}, {}),
    # --- ① 流动性容量过滤（主证伪：量化可捕获收益）---
    ("R1_adv1pct", S_BASE, {}, {"max_adv_pct": 0.01}),
    ("R2_adv5pct", S_BASE, {}, {"max_adv_pct": 0.05}),
    ("R3_adv10pct", S_BASE, {}, {"max_adv_pct": 0.10}),
    # --- ② P10 式风控栈 单项消融 ---
    ("R4_sl8", S_RISK, {"stop_loss_pct": 0.08}, {}),
    ("R5_gate_hold_ma200", S_RISK, {"market_gate": 1, "ma_window": 200, "gate_mode": "hold"}, {}),
    ("R6_gate_exit_ma200", S_RISK, {"market_gate": 1, "ma_window": 200, "gate_mode": "exit"}, {}),
    ("R7_n20", S_RISK, {"n_hold": 20, "max_positions": 20}, {}),
    ("R8_n30", S_RISK, {"n_hold": 30, "max_positions": 30}, {}),
    ("R9_everst", S_RISK, {"ever_st_exclude": 1}, {}),
    # --- ③ 组合（基于单项：止损唯一净正项；门控负优化；曾ST无效果）---
    ("R10_stack_sl8_n20", S_RISK, {"stop_loss_pct": 0.08, "n_hold": 20, "max_positions": 20}, {}),
    ("R11_stack_sl8_n30", S_RISK, {"stop_loss_pct": 0.08, "n_hold": 30, "max_positions": 30}, {}),
    ("R12_stack_sl8_n20_adv5", S_RISK, {"stop_loss_pct": 0.08, "n_hold": 20, "max_positions": 20},
     {"max_adv_pct": 0.05}),
    # --- ④ 小资金对照：容量审计理论可部署上限（组合约6万元）---
    ("R13_adv10_cap60k", S_BASE, {}, {"max_adv_pct": 0.10, "_initial_cash": 60000.0}),
]

ANCHOR = {"total_return": 9.031255, "cagr_like_cagr": None,
          "max_drawdown": -0.326496, "sharpe": 1.369306}


def yearly_returns(equity_rows):
    df = pd.DataFrame(equity_rows)
    if "date" not in df.columns or "total_asset" not in df.columns:
        return {}
    df = df.sort_values("date")
    df["year"] = df["date"].astype(str).str[:4]
    out = {}
    for y, g in df.groupby("year"):
        eq = g["total_asset"].astype(float)
        if len(eq) < 2:
            continue
        out[y] = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    return out


def max_dd_series(equity_rows):
    eq = pd.Series([float(r.get("total_asset", np.nan)) for r in equity_rows])
    eq = eq.dropna()
    if len(eq) < 2:
        return None, None
    peak = eq.cummax()
    dd = eq / peak - 1.0
    i_min = int(dd.idxmin())
    return float(dd.min()), str(equity_rows[i_min].get("date", ""))


def run_variant(tag, strat_name, reader, uni_codes, cfg, sp_over, ex_over):
    from backtest.engine import run_backtest

    sp = dict(cfg["strategy_params"])
    sp.update(sp_over)
    ex = dict(cfg["execution"])
    ex.update(ex_over)
    bt = cfg["backtest"]
    initial_cash = float(ex.pop("_initial_cash", bt["initial_cash"]))

    t0 = time.time()
    result = run_backtest(
        reader=reader,
        universe=uni_codes,
        start_date=bt["start_date"],
        end_date=bt["end_date"],
        strategy_config=sp,
        execution_cfg=ex,
        initial_cash=initial_cash,
        aux_data=AUX_GLOBAL,
        benchmark_code=bt["benchmark_code"],
        benchmark_db_path=bt["benchmark_db_path"],
        config_name="riskstack_%s" % tag,
        strategy_name=strat_name,
        trading_model=cfg["trading_model"],
    )
    perf = result["summary"]["performance"]
    runtime = time.time() - t0

    row = {
        "tag": tag,
        "total_return": perf.get("total_return"),
        "cagr": perf.get("cagr"),
        "annual_linear": perf.get("annual_return"),
        "max_drawdown": perf.get("max_drawdown"),
        "sharpe": perf.get("sharpe"),
        "cagr_calmar": perf.get("cagr_calmar"),
        "win_rate": perf.get("win_rate"),
        "n_trades": perf.get("n_trades"),
        "unfilled_orders": result["summary"]["diagnostics_aggregate"].get(
            "unfilled_order_count"),
        "runtime_s": round(runtime, 1),
    }
    yr = yearly_returns(result["equity_rows"])
    for y in sorted(yr.keys()):
        row["y%s" % y] = round(yr[y], 4)
    mdd_v, mdd_d = max_dd_series(result["equity_rows"])

    od = os.path.join(OUT_DIR, tag)
    os.makedirs(od, exist_ok=True)
    pd.DataFrame(result["equity_rows"]).to_csv(
        os.path.join(od, "equity_curve.csv"), index=False)
    pd.DataFrame(result["trades"]).to_csv(os.path.join(od, "trades.csv"), index=False)

    # 拒单/未成交原因分布（capacity_exceeded 等主证伪关键证据）
    reason_counts = {}
    for line in result.get("logs", []):
        if "unfilled_order" in line and "reason=" in line:
            r = line.split("reason=")[-1].strip()
            reason_counts[r] = reason_counts.get(r, 0) + 1
    with open(os.path.join(od, "logs.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(result.get("logs", [])))
    row["capacity_rejects"] = reason_counts.get("capacity_exceeded", 0)

    meta = {
        "tag": tag, "strategy": result["summary"].get("config_name"),
        "strategy_params": {k: v for k, v in sp.items() if not k.startswith("_")},
        "execution": ex,
        "performance": perf,
        "mdd_trough_date": mdd_d,
        "yearly_returns": yr,
        "portfolio_end": result["summary"].get("portfolio_end"),
        "unfilled_reason_counts": reason_counts,
        "runtime_seconds": round(runtime, 1),
    }
    with open(os.path.join(od, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, default=str)

    print("[%-22s] CAGR=%+.2f%% MDD=%.2f%% sharpe=%.3f trades=%s unfilled=%s (%.0fs)"
          % (tag, (perf.get("cagr") or 0) * 100,
             (perf.get("max_drawdown") or 0) * 100,
             perf.get("sharpe") or 0, perf.get("n_trades"),
             row["unfilled_orders"], runtime), flush=True)
    return row


AUX_GLOBAL = None


def main():
    global AUX_GLOBAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-aux", action="store_true")
    ap.add_argument("--only", type=str, default="")
    args = ap.parse_args()

    import yaml
    with open(CFG_YAML, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    from data.astock_reader import AstockParquetReader
    from data.universe import load_universe

    uni = load_universe(cfg["universe"]["csv"])
    uni_codes = uni["codes"]
    print(f"[cfg] universe {len(uni_codes)} codes", flush=True)

    reader = AstockParquetReader(cfg["data"]["path"], adjustment=cfg["data"]["adjustment"])

    only = set(x.strip() for x in args.only.split(",") if x.strip())
    rows = []
    for tag, strat_name, sp_over, ex_over in VARIANTS:
        if only and tag not in only:
            continue
        AUX_GLOBAL = load_aux(rebuild=args.build_aux)
        rows.append(run_variant(tag, strat_name, reader, uni_codes, cfg,
                                sp_over, ex_over))

    # R0 管线回归闸
    r0 = next((r for r in rows if r["tag"] == "R0_base_repro"), None)
    if r0 is not None:
        ok = (abs((r0["total_return"] or 0) - ANCHOR["total_return"]) < 1e-4
              and abs((r0["max_drawdown"] or 0) - ANCHOR["max_drawdown"]) < 1e-4)
        print("[gate] R0 anchor regression %s (total_return=%.6f vs 9.031255, "
              "mdd=%.6f vs -0.326496)" % ("PASS" if ok else "FAIL",
                                          r0["total_return"], r0["max_drawdown"]), flush=True)

    df = pd.DataFrame(rows)
    header = not os.path.exists(SUMMARY_CSV)
    df.to_csv(SUMMARY_CSV, mode="a", header=header, index=False)
    print("\n[done] summary -> %s\n%s" % (SUMMARY_CSV, df.to_string(index=False)), flush=True)
    reader.close()


if __name__ == "__main__":
    main()
