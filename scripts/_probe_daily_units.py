# coding: utf-8
"""Establish the actual unit convention of D's daily file.

_probe_daily_arbitrate tried to check turnover_rate == vol/float_share*100
and got 0/5513 on BOTH sides. That is not a data error -- it means the
convention is different from the assumption. Reading the actual numbers:

  000001.SZ  vol=1,236,480  float_share=1,940,560  turnover_rate=0.6372

vol/float_share = 0.6372 exactly. So D stores turnover_rate as a FRACTION
(0.6372 = 63.72%), not as a percentage. Tushare's daily turnover_rate is
percent, but this file has been divided by 100 somewhere upstream.

Check this on the full history rather than one day: if D == vol/float_share
on essentially every row, the convention is confirmed and the 2026-08-20
E values (D/10000) are wrong by a factor of 100 relative to D's own
convention.

Read-only.
"""
import sys

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"


def load(path, cols):
    t = pq.read_table(path, columns=["trade_date"] + cols)
    d = t.column("trade_date").combine_chunks().to_numpy(
        zero_copy_only=False).astype("datetime64[D]")
    out = {"__date": d}
    for c in cols:
        out[c] = t.column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")
    return out


def check(tag, x):
    vol = x["vol"]
    fs = x["float_share"]
    fr = x["free_share"]
    tr = x["turnover_rate"]
    trf = x["turnover_rate_f"]

    ok = ~np.isnan(vol) & ~np.isnan(fs) & (fs > 0) & ~np.isnan(tr)
    f1 = vol[ok] / fs[ok]
    got = tr[ok]
    r1 = np.abs(f1 - got) / np.maximum(np.abs(got), 1e-12)
    hit1 = int((r1 <= 1e-6).sum())

    ok2 = ~np.isnan(vol) & ~np.isnan(fr) & (fr > 0) & ~np.isnan(trf)
    f2 = vol[ok2] / fr[ok2]
    got2 = trf[ok2]
    r2 = np.abs(f2 - got2) / np.maximum(np.abs(got2), 1e-12)
    hit2 = int((r2 <= 1e-6).sum())

    print("[%s] rows=%d" % (tag, len(vol)))
    print("  turnover_rate  == vol/float_share        : %d/%d"
          % (hit1, int(ok.sum())))
    print("  turnover_rate_f== vol/free_share         : %d/%d"
          % (hit2, int(ok2.sum())))
    # and the x100 / x1/100 hypotheses
    for name, num, den in (("rate", vol, fs), ("rate_f", vol, fr)):
        tcol = tr if name == "rate" else trf
        m = ~np.isnan(den) & (den > 0) & ~np.isnan(tcol)
        frac = num[m] / den[m]
        v = tcol[m]
        for label, f in (("x1", 1.0), ("x100", 100.0), ("x0.01", 0.01)):
            h = int((np.abs(frac * f - v)
                     <= 1e-6 * np.maximum(np.abs(v), 1e-12)).sum())
            print("  %-8s stored == vol/den * %-6s : %d/%d"
                  % (name, label, h, int(m.sum())))


def main():
    cols = ["vol", "float_share", "free_share", "turnover_rate",
            "turnover_rate_f"]
    print("=== D ===")
    check("D", load(D_DAILY, cols))
    print("\n=== E ===")
    check("E", load(E_DAILY, cols))
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())