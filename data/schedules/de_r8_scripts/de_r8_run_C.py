# coding: utf-8
"""DE R8 维度C（补充）：MATURE 到期卖出时刻敏感性（open / close / next_open）。

TOP_N=2、T+1 open 买入（与维度A基线一致），仅改变到期日的成交时刻：
  open     : 到期日开盘卖出（基线，与维度A一致）
  close    : 到期日收盘卖出
  next_open: 到期次日开盘卖出（资金次日才可用）
止损/止盈/追盈仍在开盘价评估（与实盘桥 tick 级的偏差为全局口径，不在此隔离）。

用法: python de_r8_run_C.py
输出: out/dimC_results.json
"""
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from de_r8_common import (MAIN_END, MAIN_START, OUT_DIR, STRATS, build_daily,
                          fmt_row, load_universe, simulate, stats)

SELL_MODES = ["open", "close", "next_open"]


def main():
    print("[C-1] 加载日线上下文 ...")
    universe = load_universe()
    ctx = build_daily(universe, MAIN_START, MAIN_END)
    results = {"window": [MAIN_START, MAIN_END], "strats": {}}
    for skey in ("V14", "G2", "ENS"):
        strat = STRATS[skey]
        pkl = os.path.join(OUT_DIR, "pools_%s_main.pkl" % strat["pool_key"])
        with open(pkl, "rb") as f:
            blob = pickle.load(f)
        dates, per_day = blob["dates"], blob["per_day"]
        print("[C-2] %s: N=%d, K=2, 到期卖出时刻对照 ..." % (skey, strat["hold"]))
        rows = {}
        for sm in SELL_MODES:
            trades, ret, info = simulate(dates, per_day, 2, strat["hold"], ctx,
                                         buy_mode=("open", None), mature_sell=sm)
            st = stats(trades, ret, info)
            rows[sm] = st
            print("    到期卖出 %-9s: %s | 交易%d笔" % (sm, fmt_row(st), st["n_trades"]))
        results["strats"][skey] = {"label": strat["label"], "hold": strat["hold"], "rows": rows}

    out = os.path.join(OUT_DIR, "dimC_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("    结果:", out)


if __name__ == "__main__":
    main()
