# coding: utf-8
"""Locate exactly which trade dates E's daily disagrees with D on.

_ probe_daily_epsilon showed material disagreement on 28765 cells, all
concentrated in a handful of 2026 dates. Before deciding "replace wholesale",
we need the exact affected-date set: if it is one contiguous recent window we
can merge (D wins outside it), if it is scattered we cannot.

For each trade date this reports how many of the 6 price/valuation columns
disagree materially (>1e-6 relative). Dates with zero are clean.

Read-only.
"""
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

COLS = ["turnover_rate", "volume_ratio", "pe_ttm", "pb", "circ_mv",
        "up_limit", "down_limit", "total_share"]


def keys(path):
    t = pq.read_table(path, columns=["trade_date", "ts_code"])
    d = t.column("trade_date").combine_chunks().to_numpy(
        zero_copy_only=False).astype("datetime64[D]").astype(np.int64)
    dd = pc.dictionary_encode(t.column("ts_code").combine_chunks())
    uniq = dd.dictionary.to_pylist()
    gid = dd.indices.to_numpy(zero_copy_only=False).astype(np.int32)
    return d, gid, uniq


def main():
    dd, dg, du = keys(D_DAILY)
    ed, eg, eu = keys(E_DAILY)
    allc = sorted(set(du) | set(eu))
    pos = {c: i for i, c in enumerate(allc)}
    dg = np.fromiter((pos[c] for c in du), np.int32, len(du))[dg]
    eg = np.fromiter((pos[c] for c in eu), np.int32, len(eu))[eg]
    mult = np.int64(max(len(allc), 10000))
    dk = dd * mult + dg.astype(np.int64)
    ek = ed * mult + eg.astype(np.int64)
    od = np.argsort(dk, kind="stable")
    oe = np.argsort(ek, kind="stable")
    pr = np.searchsorted(ek[oe], dk[od])
    np.clip(pr, 0, len(oe) - 1, out=pr)
    cm = ek[oe][pr] == dk[od]
    sd = od[np.flatnonzero(cm)]
    se = oe[pr[cm]]
    days = dk[od][np.flatnonzero(cm)] // mult
    ts = pd.to_datetime(days * 86400 * 10 ** 9)
    print("common=%d" % len(sd), flush=True)

    fd = pq.ParquetFile(D_DAILY)
    fe = pq.ParquetFile(E_DAILY)

    mat = np.zeros(len(sd), dtype=bool)
    per_col = {}
    for c in COLS:
        a = fd.read(columns=[c]).column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")[sd]
        b = fe.read(columns=[c]).column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")[se]
        m = ~np.isnan(a) & ~np.isnan(b)
        av, bv = a[m], b[m]
        scale = np.maximum(np.abs(av), np.abs(bv))
        rel = np.abs(av - bv) / np.where(scale > 0, scale, 1.0)
        bad = np.zeros(len(sd), dtype=bool)
        bad[m] = (av != bv) & (rel > 1e-6)
        per_col[c] = bad
        mat |= bad
        print("  %-16s material=%d" % (c, int(bad.sum())), flush=True)

    print("\ntotal material rows=%d" % int(mat.sum()))
    if not mat.any():
        print("VERDICT=NO_MATERIAL_DIFF")
        return 0

    dts = pd.Series(ts[mat]).value_counts().sort_index()
    print("affected trade dates=%d" % len(dts))
    print("\n%-12s %8s  %s" % ("date", "nrows", "cols_differing"))
    for d, n in dts.items():
        sel = ts == d
        cols = [c for c in COLS if per_col[c][sel].any()]
        print("%-12s %8d  %s" % (str(d.date()), n, ",".join(cols)))

    print("\n=== all D trade dates vs E coverage (last 40 D dates) ===")
    dcount = pd.Series(pd.to_datetime(dd * 86400 * 10 ** 9)).value_counts()
    ecount = pd.Series(pd.to_datetime(ed * 86400 * 10 ** 9)).value_counts()
    tail = sorted(dcount.index)[-40:]
    print("%-12s %8s %8s %8s" % ("date", "D_rows", "E_rows", "delta"))
    for d in tail:
        dn = int(dcount.get(d, 0))
        en = int(ecount.get(d, 0))
        flag = "" if dn == en else ("  <== D-only" if en == 0 else "  <== diff")
        print("%-12s %8d %8d %8d%s"
              % (str(pd.Timestamp(d).date()), dn, en, dn - en, flag))
    print("\nDONE")
    return 1


if __name__ == "__main__":
    sys.exit(main())