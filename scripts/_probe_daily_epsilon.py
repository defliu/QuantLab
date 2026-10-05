# coding: utf-8
"""Explain the gap between exact-bit and tolerance comparison of D/E daily.

_probe_daily_full.py used `a != b` (exact float64 bit equality) and reported
turnover_rate mismatch=16714. _probe_daily_regime.py used
np.isclose(rtol=1e-9) and found only 5618 real differences, of which 5248
are the 2026-08-20 unit bug. The ~11k gap is therefore neither "different
data" nor "identical" -- it is last-bit representation noise.

This probe sorts every mismatching row into mutually exclusive buckets so we
know whether the gap is benign float noise or a genuine silent drift:

  unit100   E = D/100 exactly (the 2026-08-20 bug)
  exact     bit-identical except NaN handling      -> cannot happen here
  eps1e-12  |D-E| <= 1e-12 * max(|D|,|E|)          -> float64 noise
  eps1e-9   |D-E| <= 1e-9  * max(|D|,|E|)          -> float32-ish noise
  eps1e-6   |D-E| <= 1e-6  * max(|D|,|E|)          -> rounding drift
  material  everything else                         -> real disagreement

Read-only.
"""
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

COLS = ["turnover_rate", "turnover_rate_f", "volume_ratio", "pe", "pe_ttm",
        "pb", "total_mv", "circ_mv", "float_share", "total_share",
        "free_share", "dv_ratio", "dv_ttm", "up_limit", "down_limit",
        "listed_days"]


def keys(path):
    t = pq.read_table(path, columns=["trade_date", "ts_code"])
    d = t.column("trade_date").combine_chunks().to_numpy(
        zero_copy_only=False).astype("datetime64[D]").astype(np.int64)
    dd = pc.dictionary_encode(t.column("ts_code").combine_chunks())
    uniq = dd.dictionary.to_pylist()
    gid = dd.indices.to_numpy(zero_copy_only=False).astype(np.int32)
    return d, gid, uniq


def aligned():
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
    return sd, se, pd.to_datetime(days * 86400 * 10 ** 9)


def main():
    sd, se, ts = aligned()
    print("common=%d" % len(sd), flush=True)
    fd = pq.ParquetFile(D_DAILY)
    fe = pq.ParquetFile(E_DAILY)

    hdr = ("%-16s %8s %8s %8s %8s %8s %8s %9s"
           % ("col", "exactneq", "unit100", "eps1e-12",
              "eps1e-9", "eps1e-6", "material", "medDate"))
    print("\n" + hdr)
    print("-" * len(hdr))

    totals = {"unit100": 0, "noise": 0, "material": 0}
    for c in COLS:
        a = fd.read(columns=[c]).column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")[sd]
        b = fe.read(columns=[c]).column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")[se]
        na, nb = np.isnan(a), np.isnan(b)
        m = ~na & ~nb
        exactneq = 0
        unit100 = eps12 = eps9 = eps6 = material = 0
        mat_dates = []
        if m.any():
            av, bv = a[m], b[m]
            neq = av != bv
            exactneq = int(neq.sum())
            scale = np.maximum(np.abs(av), np.abs(bv))
            rel = np.abs(av - bv) / np.where(scale > 0, scale, 1.0)
            u100 = neq & np.isclose(bv * 100.0, av, rtol=1e-4, atol=1e-9)
            e12 = neq & ~u100 & (rel <= 1e-12)
            e9 = neq & ~u100 & ~e12 & (rel <= 1e-9)
            e6 = neq & ~u100 & ~e12 & ~e9 & (rel <= 1e-6)
            mat = neq & ~u100 & ~e12 & ~e9 & ~e6
            unit100, eps12, eps9, eps6, material = (
                int(u100.sum()), int(e12.sum()), int(e9.sum()),
                int(e6.sum()), int(mat.sum()))
            if mat.any():
                mt = ts[m][mat]
                mat_dates = [str(x.date()) for x in
                             pd.Series(mt).value_counts().sort_index()
                             .tail(3).index]
        noise = eps12 + eps9 + eps6
        totals["unit100"] += unit100
        totals["noise"] += noise
        totals["material"] += material
        print("%-16s %8d %8d %8d %8d %8d %8d %9s"
              % (c, exactneq, unit100, eps12, eps9, eps6, material,
                 ",".join(mat_dates) if mat_dates else "-"), flush=True)

    print("\n==== summary over %d numeric columns ====" % len(COLS))
    print("unit100 (E=D/100)      = %d" % totals["unit100"])
    print("float noise (<=1e-6)   = %d" % totals["noise"])
    print("material disagreement  = %d" % totals["material"])
    if totals["material"]:
        print("\nVERDICT=REVIEW_REQUIRED")
        return 1
    print("\nVERDICT=NO_MATERIAL_DIFF_EXCEPT_UNIT100")
    return 0


if __name__ == "__main__":
    sys.exit(main())