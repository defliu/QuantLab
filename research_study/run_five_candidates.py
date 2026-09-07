# coding: utf-8
"""五条可落地策略统一回测运行器（T-20260829 全套）。

  1. 涨跌停掩码前置：ATR 基线 vs 掩码版 对比（T-001）
  2. 闭环波动率控制：引用已完成的离线实验结论（T-002，证伪）
  3. OMD 马尔可夫排名链选股（T-003）
  4. 小市值 + 扩散指数择时（T-004）
  5. ETF 多资产回撤买入（T-005，自含轻量回测）

统一口径：next_open、滑点 10bp、佣金 2.5bp、卖印 10bp、整手 100、涨跌停/ST/停牌拒单。
窗口：2016-01-04 ~ 2026-08-21。基准 000300.SH。
"""
import os
import sys
import json

import numpy as np
import pandas as pd

PROJECT_ROOT = "D:/QuantLab"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from data.astock_reader import AstockParquetReader
from data.mask_reader import MaskParquetReader
from data.universe import load_universe
from backtest.engine import run_backtest
from backtest.portfolio import Portfolio
from backtest.execution import fill_buy, fill_sell
from backtest.analyzer import compute_metrics

START = "2016-01-04"
END = "2026-08-21"
INIT_CASH = 1_000_000.0
EXEC_CFG = {
    "price": "next_open",
    "slippage": 0.001,
    "commission_rate": 0.00025,
    "tax_rate": 0.0001,
}
BENCH = "000300.SH"
BENCH_DB = "D:/astock/benchmark/benchmark_index.duckdb"
UNI_CSV = "D:/QuantLab/config/full_a_sh_sz.csv"

# 触发策略模块注册
import strategy.atr_mask            # noqa: F401
import strategy.omd_markov          # noqa: F401
import strategy.smallcap_diffusion  # noqa: F401


def _run_stock(strategy_name, strategy_config, reader_cls, name):
    uni = load_universe(UNI_CSV)
    universe = uni["codes"]
    reader = reader_cls("D:/astock/daily/stock_daily.parquet", adjustment="hfq")
    try:
        res = run_backtest(
            reader=reader,
            universe=universe,
            start_date=START,
            end_date=END,
            strategy_config=strategy_config,
            execution_cfg=EXEC_CFG,
            initial_cash=INIT_CASH,
            benchmark_code=BENCH,
            benchmark_db_path=BENCH_DB,
            config_name=name,
            strategy_name=strategy_name,
            trading_model="next_open",
        )
    finally:
        reader.close()
    p = res["summary"]["performance"]
    return p


# ----------------------------------------------------------------------
# ETF 多资产回撤买入（自含轻量回测）
# ----------------------------------------------------------------------
ETF_CODES = ["510300.SH", "510500.SH", "159915.SZ",
             "518880.SH", "511010.SH", "511260.SH", "159985.SZ"]
ETF_DIR = "D:/QuantLab/data/etf_daily"


def _load_etf():
    data = {}
    for c in ETF_CODES:
        fn = c.replace(".", "_") + ".csv"
        df = pd.read_csv(os.path.join(ETF_DIR, fn))
        df = df.sort_values("date").reset_index(drop=True)
        data[c] = df
    return data


def run_etf_pullback(n_down):
    data = _load_etf()
    cal = sorted(set().union(*[set(df["date"]) for df in data.values()]))
    cal = [d for d in cal if START <= d <= END]
    idx_of = {d: i for i, d in enumerate(cal)}

    # 对齐 close 到日历
    close_cal = {}
    for c in ETF_CODES:
        df = data[c]
        m = {dd: vv for dd, vv in zip(df["date"], df["close"].astype(float))}
        open_m = {dd: vv for dd, vv in zip(df["date"], df["open"].astype(float))}
        high_m = {dd: vv for dd, vv in zip(df["date"], df["high"].astype(float))}
        low_m = {dd: vv for dd, vv in zip(df["date"], df["low"].astype(float))}
        close_cal[c] = (m, open_m, high_m, low_m)

    # 计算每日触发信号（基于当日收盘）
    signals = {}
    for i, d in enumerate(cal):
        trig = set()
        for c in ETF_CODES:
            m, _, _, _ = close_cal[c]
            ser = [m.get(cal[j]) for j in range(max(0, i - 260), i + 1)]
            ser = [x for x in ser if x is not None and not (isinstance(x, float) and np.isnan(x))]
            if len(ser) < 200:
                continue
            price = m.get(d)
            if price is None:
                continue
            ma200 = float(np.mean(ser[-200:]))
            if price <= ma200:
                continue
            ok = True
            for k in range(1, n_down + 1):
                if i - k < 0:
                    ok = False
                    break
                a = m.get(cal[i - k + 1])
                b = m.get(cal[i - k])
                if a is None or b is None or a >= b:
                    ok = False
                    break
            if ok:
                trig.add(c)
        signals[d] = trig

    pf = Portfolio(INIT_CASH)
    equity_rows = []
    trades = []
    pending = None
    n_days = len(cal)

    def _bar(c, d):
        m, om, hm, lm = close_cal[c]
        return {
            "date": d,
            "open": om.get(d, np.nan),
            "high": hm.get(d, np.nan),
            "low": lm.get(d, np.nan),
            "close": m.get(d, np.nan),
            "is_st": False,
        }

    def _market(d):
        return {c: pd.DataFrame([_bar(c, d)]) for c in ETF_CODES}

    for i, d in enumerate(cal):
        if i > 0:
            pf.advance_holding_days()
        if pending is not None:
            for dec in pending.get("sell_decisions", []):
                pos = pf.positions.get(dec["code"])
                if pos is None:
                    continue
                pos_arg = {"code": dec["code"], "volume": int(pos["volume"]),
                           "available_volume": int(pos["available_volume"]),
                           "cost_price": float(pos["cost_price"]),
                           "entry_date": pos["entry_date"],
                           "holding_days": int(pos["holding_days"]),
                           "last_price": float(pos["last_price"]),
                           "unrealized_pnl": float(pos["unrealized_pnl"])}
                tr, _ = fill_sell(dec, pos_arg, _market(d), d, EXEC_CFG, "etf")
                if tr is not None:
                    pf.apply_trade(tr)
                    trades.append(tr)
            for cand in pending.get("buy_candidates", []):
                tr, _ = fill_buy(cand, _market(d), d, EXEC_CFG, "etf")
                if tr is not None:
                    pf.apply_trade(tr)
                    trades.append(tr)
        pf.mark_to_market(_market(d), d)
        equity_rows.append(pf.equity_row("etf", d))

        # 用当日信号构造下一交易日目标
        trig = signals[d]
        held = set(pf.positions.keys())
        sell_decisions = []
        buy_candidates = []
        for c in held:
            if c not in trig:
                sell_decisions.append({"code": c, "reason": "pullback_exit", "layer": "confirm"})
        if trig:
            w = 1.0 / len(trig)
            for c in trig:
                tc = w * pf.total_asset()
                if c in held:
                    continue
                buy_candidates.append({"code": c, "target_cash": tc,
                                       "reason": "pullback_entry", "layer": "confirm", "rank": 0})
        pending = {"sell_decisions": sell_decisions, "buy_candidates": buy_candidates}

    perf = compute_metrics(equity_rows, trades, cal, INIT_CASH,
                           open_positions=pf.position_list())
    # 持仓时间占比（粗略：每日有持仓的天数 / 总天数）
    invested_days = sum(1 for r in equity_rows if r["market_value"] > 1.0)
    perf["invested_days_ratio"] = round(invested_days / n_days, 4)
    return perf


def main():
    results = {}

    # 1) ATR 基线
    atr_cfg = {
        "rebalance_freq": "quarterly", "n_hold": 100, "atr_win": 14,
        "atr_pct_max": 0.06, "turnover_min": 1.0, "turnover_max": 8.0,
        "quality_gate": 1, "momentum_gate": 1, "stop_loss": -0.08,
        "position_sizing": "equal", "target_leverage": 1.0, "vol_target": 0.0,
        "industry_cap": 0.0, "max_positions": 0, "min_position_value": 0.0,
        "vol_window": 60, "margin_interest_rate": 0.06,
    }
    print("[run] ATR baseline ...")
    results["ATR_baseline"] = _run_stock("atr_lowvol", atr_cfg,
                                         AstockParquetReader, "atr_baseline")

    # 1b) ATR 掩码版
    print("[run] ATR mask ...")
    results["ATR_mask"] = _run_stock("atr_lowvol_mask", atr_cfg,
                                     MaskParquetReader, "atr_mask")

    # 3) OMD
    omd_cfg = {"position_sizing": "equal", "target_leverage": 1.0,
               "max_positions": 0, "min_position_value": 0.0}
    print("[run] OMD markov ...")
    results["OMD"] = _run_stock("omd_markov", omd_cfg,
                                AstockParquetReader, "omd_markov")

    # 4) 小市值扩散指数
    sc_cfg = {"position_sizing": "equal", "target_leverage": 1.0,
              "max_positions": 200, "min_position_value": 0.0}
    print("[run] smallcap diffusion ...")
    results["SmallCap"] = _run_stock("smallcap_diffusion", sc_cfg,
                                     AstockParquetReader, "smallcap_diffusion")

    # 5) ETF 回撤买入（扫描 N=2..5）
    print("[run] ETF pullback ...")
    etf = {}
    for n in (2, 3, 4, 5):
        etf["ETF_N%d" % n] = run_etf_pullback(n)
    results["ETF"] = etf

    # 打印
    keys = ["total_return", "cagr", "max_drawdown", "sharpe", "calmar",
            "win_rate", "turnover_annualized", "n_trades", "excess_return",
            "information_ratio", "invested_days_ratio"]
    print("\n================ 回测结果汇总 ================")
    print("窗口 %s ~ %s  基准 %s  初始 %d" % (START, END, BENCH, INIT_CASH))
    print("策略".ljust(16), *(k[:14].rjust(15) for k in keys))
    for name, p in results.items():
        if name == "ETF":
            for nn, pp in p.items():
                row = [("%.4f" % pp.get(k, 0) if isinstance(pp.get(k), (int, float)) else "-") for k in keys]
                print(nn.ljust(16), *[v.rjust(15) for v in row])
            continue
        row = [("%.4f" % p.get(k, 0) if isinstance(p.get(k), (int, float)) else str(p.get(k))) for k in keys]
        print(name.ljust(16), *[v.rjust(15) for v in row])

    with open("D:/QuantLab/research_study/five_candidates_result.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("\n结果已写入 research_study/five_candidates_result.json")


if __name__ == "__main__":
    main()
