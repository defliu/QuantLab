# coding: utf-8
"""DE R8 维度B：买入时刻对照（TOP_N=2 固定，各策略自身持有期）。

买入时刻（均为信号日 T 的次一交易日 T+1 内）：
  次日open / 09:35 / 10:00 / 10:05 / 10:10 / 10:30 / 14:45 / 次日close
分钟价：D:/astock/min_1min 对应时刻 bar close（不复权，与日线口径一致）；
  无分钟文件/日期 → 回退次日open（计数披露）；分钟bar无量或价≥涨停价 → 放弃该候选买下一只。
卖出侧统一为到期/止损/止盈于日线 open 评估（仅隔离买入时刻的影响）。

用法: python de_r8_run_B.py
输出: out/dimB_results.json
"""
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from de_r8_common import (MAIN_END, MAIN_START, MIN_TIMES, MinuteBook,
                          OUT_DIR, STRATS, build_daily, fmt_row, load_universe,
                          simulate, stats)

MODES = [("open", None, "次日open")] + [("minute", t, t) for t in MIN_TIMES] + \
        [("close", None, "次日close")]


def main():
    print("[B-1] 加载日线上下文 ...")
    universe = load_universe()
    ctx = build_daily(universe, MAIN_START, MAIN_END)
    book = MinuteBook()

    results = {"window": [MAIN_START, MAIN_END], "strats": {}}
    for skey in ("V14", "G2", "ENS"):
        strat = STRATS[skey]
        pkl = os.path.join(OUT_DIR, "pools_%s_main.pkl" % strat["pool_key"])
        with open(pkl, "rb") as f:
            blob = pickle.load(f)
        dates, per_day = blob["dates"], blob["per_day"]
        print("[B-2] %s: %d 信号日, N=%d, K=2, 买入时刻网格 ..." % (skey, len(dates), strat["hold"]))
        rows = {}
        pairs = set()
        for mode, tt, name in MODES:
            if mode == "open":
                trades, ret, info = simulate(dates, per_day, 2, strat["hold"], ctx,
                                             buy_mode=("open", None))
                st = stats(trades, ret, info)
                st["fallback"] = 0
                rows[name] = st
                print("    买入时刻 %-8s: %s | 交易%d笔" % (name, fmt_row(st), st["n_trades"]))
                continue
            if mode == "close":
                bp = []
                trades, ret, info = simulate(dates, per_day, 2, strat["hold"], ctx,
                                             buy_mode=("close", None), buy_pairs_out=bp)
                pairs.update(bp)
                st = stats(trades, ret, info)
                st["fallback"] = 0
                rows[name] = st
                print("    买入时刻 %-8s: %s | 交易%d笔" % (name, fmt_row(st), st["n_trades"]))
                continue
            # minute：两阶段（先收集缺失 pair → ensure → 重跑一次）
            miss = []
            trades, ret, info = simulate(dates, per_day, 2, strat["hold"], ctx,
                                         buy_mode=("minute", tt), minute_book=book,
                                         missing_out=miss)
            if miss:
                book.ensure(miss)
                miss2 = []
                trades, ret, info = simulate(dates, per_day, 2, strat["hold"], ctx,
                                             buy_mode=("minute", tt), minute_book=book,
                                             missing_out=miss2)
            st = stats(trades, ret, info)
            st["fallback"] = info["n_minute_fallback"]
            st["minute_block"] = info["n_minute_block"]
            rows[name] = st
            print("    买入时刻 %-8s: %s | 交易%d笔 回退open %d次 分钟拦截 %d次" % (
                name, fmt_row(st), st["n_trades"], st["fallback"], st["minute_block"]))
        print("    [分钟文件加载 %d 个 | 无覆盖pair %d 个]" % (book.n_files, len(book.missing)))
        results["strats"][skey] = {"label": strat["label"], "hold": strat["hold"], "rows": rows}

    out = os.path.join(OUT_DIR, "dimB_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print("    结果:", out)


if __name__ == "__main__":
    main()
