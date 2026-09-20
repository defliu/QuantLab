# coding: utf-8
"""DE R8 维度A：三策略 TOP_N∈{1..6} 持仓数网格回测（主窗口 + V1.4 近期段）。

用法: python de_r8_run_A.py
输出: out/dimA_results.json + pools pickle（供维度B/C 复用）
"""
import json
import os
import pickle
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from de_r8_common import (MAIN_END, MAIN_START, OUT_DIR, STRATS, build_daily,
                          build_pools, fmt_row, load_universe, simulate, stats)

KS = [1, 2, 3, 4, 5, 6]
RECENT_START, RECENT_END = "2026-08-15", "2026-09-10"  # 执行价(daily)只到 2026-09-11


def run_grid(skey, dates, per_day, ctx, ks=KS):
    strat = STRATS[skey]
    rows = []
    for K in ks:
        trades, ret, info = simulate(dates, per_day, K, strat["hold"], ctx)
        st = stats(trades, ret, info)
        st["K"] = K
        rows.append(st)
        print("    [%s] K=%d N=%d: %s | 交易%d笔 出场%s" % (
            skey, K, strat["hold"], fmt_row(st), st["n_trades"],
            " ".join("%s=%d" % kv for kv in sorted(st["reasons"].items()))))
    return rows


def main():
    print("[A-1] 加载日线上下文（universe + 主窗口 %s ~ %s）..." % (MAIN_START, MAIN_END))
    universe = load_universe()
    print("    universe %d 只" % len(universe))
    ctx = build_daily(universe, MAIN_START, MAIN_END)

    results = {"window": [MAIN_START, MAIN_END], "strats": {}}
    for skey in ("V14", "G2", "ENS", "ENS_FUSION"):
        strat = STRATS[skey]
        print("[A-2] %s 构建逐日信号池 ..." % skey)
        dates, per_day, pstats = build_pools(skey, MAIN_START, MAIN_END, ctx)
        print("    %s: %d 信号日 | 红线内日均过线 %.1f 只" % (
            skey, len(dates), pstats["avg_passed"]))
        with open(os.path.join(OUT_DIR, "pools_%s_main.pkl" % strat["pool_key"]), "wb") as f:
            pickle.dump({"dates": dates, "per_day": per_day}, f)
        print("[A-3] %s K 网格 ..." % skey)
        rows = run_grid(skey, dates, per_day, ctx)
        results["strats"][skey] = {"label": strat["label"], "hold": strat["hold"],
                                   "avg_passed": pstats["avg_passed"], "rows": rows}

    # ---- 近期段稳健性交叉（仅 V1.4：G2/ENS 面板冻结至 2026-08-14，SKIP）----
    print("[A-4] 近期段 %s ~ %s（V1.4 only；G2/ENS 面板冻结至 2026-08-14 → SKIP）" % (
        RECENT_START, RECENT_END))
    uni14 = set(pd.read_parquet(STRATS["V14"]["panel"], columns=["ts_code"])["ts_code"].astype(str))
    ctx2 = build_daily(uni14, RECENT_START, RECENT_END)
    dates2, per_day2, pstats2 = build_pools("V14", RECENT_START, RECENT_END, ctx2)
    print("    V14 近期段: %d 信号日 | 过线 %.1f 只/日" % (len(dates2), pstats2["avg_passed"]))
    rows2 = run_grid("V14", dates2, per_day2, ctx2)
    results["recent"] = {"window": [RECENT_START, RECENT_END], "note": "仅V1.4；G2/ENS面板冻结至2026-08-14",
                         "V14": {"rows": rows2, "avg_passed": pstats2["avg_passed"]}}

    out = os.path.join(OUT_DIR, "dimA_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("    结果:", out)


if __name__ == "__main__":
    main()
