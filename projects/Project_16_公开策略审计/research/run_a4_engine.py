# coding: utf-8
"""任务B A4：菜场大妈六步选股 干净口径 —— 框架引擎薄入口 (T-20260819-002)

走 backtest.engine.run_backtest（只读 import，禁改 backtest/）：
  - 宇宙: universe_all_a.csv（全A静态池；退市股在 astock 历史 bar 内仍在，
    宇宙列表不含退市股，但退市股 bar 亦不在 reader 中 —— 修正后与 A1/A2/A3
    "当日有行情全池"等价，见报告口径段）
  - aux_data 传递 reader 不含的字段：total_mv / 自建TTM股息率 / PIT净利同比 / listed_days
  - 成本/涨跌停/停牌/整手 由引擎 execution 层承担
"""
import os
import sys
import time

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, r"D:\QuantLab")

RESULTS = os.path.join(PROJ, "results")
os.makedirs(RESULTS, exist_ok=True)
BASE = r"E:/astock"

# 注册本策略进 registry（薄入口负责，避免改全局 strategy/ 目录）
import importlib.util
_strat_path = os.path.join(PROJ, "strategy", "cai_market_audit.py")
_spec = importlib.util.spec_from_file_location("cai_market_audit", _strat_path)
_strat_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_strat_mod)
from strategy.registry import get_strategy
assert get_strategy("cai_market_audit") is not None


def build_aux():
    """构建 aux_data：total_mv / 自建TTM / PIT净利同比 / listed_days。"""
    t0 = time.time()
    sd = pd.read_parquet(
        f"{BASE}/daily/stock_daily.parquet",
        columns=["trade_date", "ts_code", "close", "total_mv"])
    if isinstance(sd.index, pd.MultiIndex):
        sd = sd.reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd = sd[(sd["trade_date"] >= "2017-12-31")].copy()

    # 1) total_mv 宽表 (index=date, columns=code)
    mv_wide = sd.pivot_table(index="trade_date", columns="ts_code", values="total_mv")
    mv_wide.index = pd.DatetimeIndex(mv_wide.index).strftime("%Y-%m-%d")

    # 2) 自建 TTM 股息率（div_proc=='实施' + ex_date 窗口 365 天 / close）
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

    # 3) PIT 净利同比（f_ann_date/ann_date <= date，取最大 end_date，累计归母）
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

    # 4) list_date: {code: list_date(Timestamp)}（策略内动态算上市自然日）
    sb = pd.read_parquet(f"{BASE}/basic/stock_basic.parquet")
    sb["ts_code"] = sb["ts_code"].astype(str)
    sb["list_date"] = pd.to_datetime(sb["list_date"])
    list_date = {r.ts_code: r.list_date
                 for r in sb.itertuples() if pd.notna(r.list_date)}
    print(f"[aux] mv_wide={mv_wide.shape} dy={dy.shape} yoy={yoy_df.shape} "
          f"list_date={len(list_date)} ({time.time()-t0:.1f}s)", flush=True)
    return {"total_mv": mv_wide, "div_ttm": dy, "yoy_pit": yoy_df,
            "list_date": list_date}


def main():
    print("=" * 70, flush=True)
    print("任务B A4：菜场大妈六步选股 干净口径（框架引擎 next_open + 成本 + 涨跌停/停牌 + 整手）", flush=True)
    t0 = time.time()

    cfg_path = os.path.join(PROJ, "config", "cai_market_a4.yaml")
    import yaml
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    bt = cfg["backtest"]
    from backtest.engine import run_backtest
    from data.astock_reader import AstockParquetReader
    from data.universe import load_universe

    universe_csv = cfg["universe"]["csv"]
    uni = load_universe(universe_csv)
    universe = uni["codes"]
    print(f"[cfg] universe {len(universe)} codes", flush=True)

    reader = AstockParquetReader(cfg["data"]["path"], adjustment=cfg["data"]["adjustment"])
    aux = build_aux()
    # trading_calendar 由引擎自动注入

    result = run_backtest(
        reader=reader,
        universe=universe,
        start_date=bt["start_date"],
        end_date=bt["end_date"],
        strategy_config=cfg["strategy_params"],
        execution_cfg=cfg["execution"],
        initial_cash=float(bt["initial_cash"]),
        aux_data=aux,
        benchmark_code=bt["benchmark_code"],
        benchmark_db_path=bt["benchmark_db_path"],
        config_name=bt["name"],
        strategy_name=cfg["strategy"],
        trading_model=cfg["trading_model"],
    )
    reader.close()

    perf = result["summary"]["performance"]
    print(f"\n[A4] 引擎口径结果:")
    print(f"  total_return  = {perf.get('total_return', 0)*100:+.2f}%")
    print(f"  annual_return = {perf.get('annual_return', 0)*100:+.2f}%")
    print(f"  max_drawdown  = {perf.get('max_drawdown', 0)*100:+.2f}%")
    print(f"  sharpe        = {perf.get('sharpe', 0):.2f}")
    print(f"  n_trades      = {perf.get('n_trades', 0)}")
    if "execution" in result["summary"]:
        print(f"  exec          = {result['summary']['execution']}")

    # 落盘 KPI
    import json
    out = {
        "total_return": perf.get("total_return"),
        "annual_return": perf.get("annual_return"),
        "max_drawdown": perf.get("max_drawdown"),
        "sharpe": perf.get("sharpe"),
        "n_trades": perf.get("n_trades"),
        "run_id": result.get("run_id"),
        "benchmark_note": result.get("benchmark_note"),
    }
    with open(os.path.join(RESULTS, "taskb_A4_kpi.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    # 落盘 trades / equity / positions 供核对
    try:
        from backtest import report
        report.set_results_dir(os.path.join(RESULTS, "a4_engine_out"))
        rd = report.write_all(result, config_name=cfg["backtest"]["name"])
        print("results_dir: %s" % rd, flush=True)
    except Exception as e:
        print("report write failed: %s" % e, flush=True)
    print(f"\n[落盘] taskb_A4_kpi.json, 总用时 {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()