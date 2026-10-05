# coding: utf-8
"""Classify every D/E daily mismatch by nature, per column.

The full-file gate proved the two files are NOT interchangeable. Before any
replace decision we must know, per column, whether a mismatch is:

  unit        - E == D / 100 (or * 100): a pure scale change, silently
                reinterprets every downstream threshold
  rounding    - D == round(E, k): D was stored as a low-precision snapshot
  null        - one side missing: information loss either way
  real        - both non-null, genuinely different values
  sign        - E negative where D is null (D filtered out non-positive PE)

Anything classified as unit/sign/real must NOT be silently replaced.
Read-only.
"""
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

COLS = [
    "up_limit", "down_limit", "turnover_rate", "turnover_rate_f",
    "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm",
    "dv_ratio", "dv_ttm", "total_share", "float_share", "free_share",
    "total_mv", "circ_mv", "adj_factor", "listed_days",
]
RTOL = 1e-9
ATOL = 1e-12


def keys(path):
    t = pq.read_table(path, columns=["trade_date", "ts_code"])
    d = t.column("trade_date").combine_chunks().to_numpy(
        zero_copy_only=False).astype("datetime64[D]").astype(np.int64)
    dd = pc.dictionary_encode(t.column("ts_code").combine_chunks())
    uniq = dd.dictionary.to_pylist()
    gid = dd.indices.to_numpy(zero_copy_only=False).astype(np.int32)
    return d, gid, uniq


def col(path, name, sel):
    a = pq.ParquetFile(path).read(columns=[name]).column(name)\
        .combine_chunks().to_numpy(zero_copy_only=False).astype("float64")
    return a[sel]


def classify(a, b):
    na, nb = np.isnan(a), np.isnan(b)
    out = {}
    out["D_null_E_value"] = int((na & ~nb).sum())
    out["D_value_E_null"] = int((~na & nb).sum())
    out["both_null"] = int((na & nb).sum())
    out["both_value"] = int((~na & ~nb).sum())

    m = ~na & ~nb
    if m.any():
        av, bv = a[m], b[m]
        diff = ~np.isclose(av, bv, rtol=RTOL, atol=ATOL)
        nd = int(diff.sum())
        out["real"] = nd
        out["match"] = int(m.sum()) - nd
        r100 = np.isclose(bv * 100.0, av, rtol=1e-6, atol=1e-9)
        out["unit_x100"] = int(r100.sum())
        r001 = np.isclose(bv / 100.0, av, rtol=1e-6, atol=1e-9)
        out["unit_div100"] = int(r001.sum())
        rk = np.zeros(len(bv), dtype=bool)
        for k in range(1, 9):
            rk |= np.isclose(np.round(bv, k), av, rtol=RTOL, atol=ATOL)
        out["rounding_of_E"] = int(rk.sum())
        rkd = np.zeros(len(bv), dtype=bool)
        for k in range(1, 9):
            rkd |= np.isclose(av, np.round(av, k), rtol=RTOL, atol=ATOL)
        out["D_is_rounded"] = int(rkd.sum())
        out["sign_flip"] = int(((av > 0) & (bv <= 0)).sum()
                               + ((av <= 0) & (bv > 0)).sum())
        rel = np.abs(av - bv) / np.maximum(np.abs(av), 1e-12)
        rel = rel[diff]
        if rel.size:
            out["rel_p50"] = float(np.median(rel))
            out["rel_p99"] = float(np.percentile(rel, 99))
            out["rel_max"] = float(rel.max())
    return out


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
    dks, eks = dk[od], ek[oe]
    pr = np.searchsorted(eks, dks)
    np.clip(pr, 0, len(eks) - 1, out=pr)
    cm = eks[pr] == dks
    pd_ = od[np.flatnonzero(cm)]
    pe_ = oe[pr[cm]]
    days = dks[np.flatnonzero(cm)] // mult
    gc = dg[pd_]
    print("common=%d" % len(pd_), flush=True)

    print("\n%-16s %9s %9s %9s %9s %8s %8s %7s %7s"
          % ("col", "Dnull_E", "Dval_Enu", "bothval", "real", "unit100",
             "round(E)", "sign", "relp50"))
    for c in COLS:
        a = col(D_DAILY, c, pd_)
        b = col(E_DAILY, c, pe_)
        r = classify(a, b)
        print("%-16s %9d %9d %9d %9d %8d %8d %7d %7.4g"
              % (c, r["D_null_E_value"], r["D_value_E_null"],
                 r["both_value"], r["real"], r["unit_x100"],
                 r["rounding_of_E"], r["sign_flip"],
                 r.get("rel_p50", 0.0)), flush=True)

    print("\n--- null placement (E nulls by year) ---")
    for c in ["adj_factor", "listed_days"]:
        b = col(E_DAILY, c, pe_)
        yr = pd.to_datetime(days * 86400 * 10 ** 9).year.to_numpy()
        nb = np.isnan(b)
        print("%s: E nulls=%d" % (c, int(nb.sum())))
        if nb.any():
            uy, uc = np.unique(yr[nb], return_counts=True)
            print("   by year: %s"
                  % {int(k): int(v) for k, v in zip(uy, uc)})
        ad = col(D_DAILY, c, pd_)
        print("   D nulls=%d" % int(np.isnan(ad).sum()))

    print("\n--- adj_factor NaN codes ---")
    b = col(E_DAILY, "adj_factor", pe_)
    nb = np.isnan(b)
    for i in np.flatnonzero(nb)[:10]:
        print("   %s %s D=%r"
              % (pd.Timestamp(days[i] * 86400 * 10 ** 9).date(),
                 allc[int(gc[i])], col(D_DAILY, "adj_factor", pd_)[i]))

    print("\n--- pe negative in E, null in D ---")
    a = col(D_DAILY, "pe", pd_)
    b = col(E_DAILY, "pe", pe_)
    sel = np.isnan(a) & ~np.isnan(b) & (b < 0)
    print("   count=%d  all E negative=%s"
          % (int(sel.sum()), bool((b[sel] < 0).all()) if sel.any() else True))

    print("\n--- turnover_rate_f unit check ---")
    a = col(D_DAILY, "turnover_rate_f", pd_)
    b = col(E_DAILY, "turnover_rate_f", pe_)
    m = ~np.isnan(a) & ~np.isnan(b)
    ratio = a[m] / b[m]
    ok = np.isclose(ratio, 100.0, rtol=1e-6)
    print("   rows=%d  ratio==100 on %d  ratio p1/p50/p99=%.4g/%.4g/%.4g"
          % (int(m.sum()), int(ok.sum()),
             float(np.percentile(ratio, 1)), float(np.median(ratio)),
             float(np.percentile(ratio, 99))))
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())