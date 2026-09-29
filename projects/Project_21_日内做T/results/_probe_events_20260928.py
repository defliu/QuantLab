# -*- coding: utf-8 -*-
"""探针：验证 F1-F6 在真实 stock-day 上是否产出事件 / 是否被异常吞掉。

用法: PYTHONPATH=projects/Project_21_日内做T python results/_probe_events_20260928.py
产出: stdout 诊断报告（ctx None 率、各族事件数、各族异常计数与首条报错）
"""
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.scanner import SignalScanner  # noqa: E402

CONFIG = str(ROOT / "config" / "midvol_criteria.yaml")
DATES = ["20190102", "20190103", "20190104", "20190107", "20190108"]
N_STOCKS = 60


def main():
    t0 = time.time()
    sc = SignalScanner(CONFIG)
    sc.load_reference_data()
    pool = sc.build_midvol_pool()
    stocks = pool[:N_STOCKS]

    preload_t, _ = sc._preload_batch(
        stocks,
        __import__("pandas").Timestamp("20181101"),
        __import__("pandas").Timestamp("20190131"),
    )
    print("preload {:.1f}s for {} stocks".format(preload_t, len(stocks)))

    ctx_none = 0
    ctx_ok = 0
    short_day = 0
    fam_events = {k: 0 for k in sc.signals}
    fam_err = {k: {} for k in sc.signals}
    total_events = 0
    scan_t = time.time()

    for d in DATES:
        for code in stocks:
            try:
                ctx = sc.get_context_for(code, d)
            except Exception as ex:
                ctx_none += 1
                print("get_context_for EX {}: {} {}".format(
                    code, type(ex).__name__, str(ex)[:120]))
                continue
            if ctx is None:
                ctx_none += 1
                continue
            if len(ctx.df_1min) < 30:
                short_day += 1
                continue
            ctx_ok += 1
            for fam, sig in sc.signals.items():
                try:
                    evs = sig.compute_signals(ctx)
                    if evs:
                        fam_events[fam] += len(evs)
                        total_events += len(evs)
                except Exception as ex:
                    key = "{}: {}".format(type(ex).__name__, str(ex)[:80])
                    fam_err[fam][key] = fam_err[fam].get(key, 0) + 1

    scan_t = time.time() - scan_t
    print("\n==== 诊断结果 ====")
    print("stock-days: ok={} ctx_none={} short(<30bars)={}".format(
        ctx_ok, ctx_none, short_day))
    print("events total={}".format(total_events))
    for fam in sc.signals:
        errs = fam_err[fam]
        err_str = "; ".join("{} x{}".format(k, v) for k, v in
                            sorted(errs.items(), key=lambda kv: -kv[1])[:3])
        print("  {}: events={} err_types={} {}".format(
            fam, fam_events[fam], len(errs), err_str))
    print("scan {:.1f}s | {:.2f} ms/stock-day".format(
        scan_t, scan_t * 1000.0 / max(ctx_ok + ctx_none, 1)))
    print("total {:.1f}s".format(time.time() - t0))


if __name__ == "__main__":
    main()
