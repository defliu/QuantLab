# coding: utf-8
"""Locate the regime boundary in E's daily valuation/turnover columns.

Mismatches are not randomly distributed: samples show turnover_rate differing
by 0.2% in June 2026 but turnover_rate_f differing by exactly 100x in August
2026. That smells like E was assembled by concatenating two sources with
different units. If so, "replace wholesale" would silently rescale part of
history.

For each suspect column this prints, per month, the count of real differences
and the median D/E ratio, so a regime boundary is visible as a step.
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

COLS = ["turnover_rate", "turnover_rate_f", "volume_ratio", "pe",
        "total_mv", "adj_factor", "listed_days"]
MIN_ABS = 1e-9


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
    sd = od[np.flatnonzero(cm)]
    se = oe[pr[cm]]
    days = dks[np.flatnonzero(cm)] // mult
    ts = pd.to_datetime(days * 86400 * 10 ** 9)
    print("common=%d  range=%s ~ %s"
          % (len(sd), ts.min().date(), ts.max().date()), flush=True)

    for c in COLS:
        a = col(D_DAILY, c, sd)
        b = col(E_DAILY, c, se)
        print("\n===== %s =====" % c)
        print("%-10s %8s %8s %8s %8s %10s"
              % ("month", "bothval", "Dnull", "real", "x100", "med D/E"))
        grp = ts.to_period("M").astype(str).to_numpy()
        months = sorted(set(grp))
        for mk in months:
            if mk < "2026-01":
                continue
            m = grp == mk
            am, bm = a[m], b[m]
            na, nb = np.isnan(am), np.isnan(bm)
            both = ~na & ~nb
            real = both & ~np.isclose(am, bm, rtol=1e-9, atol=1e-12)
            x100 = real & np.isclose(bm * 100.0, am, rtol=1e-4, atol=1e-9)
            ratio = ""
            if real.any():
                av, bv = am[real], bm[real]
                use = (np.abs(av) > MIN_ABS) & (np.abs(bv) > MIN_ABS)
                if use.any():
                    r = av[use] / bv[use]
                    ratio = "%.4g" % float(np.median(r))
            print("%-10s %8d %8d %8d %8d %10s"
                  % (mk, int(both.sum()), int((na & ~nb).sum()),
                     int(real.sum()), int(x100.sum()), ratio))

    for name in ("turnover_rate_f", "turnover_rate"):
        a = col(D_DAILY, name, sd)
        b = col(E_DAILY, name, se)
        real = (~np.isnan(a) & ~np.isnan(b)
                & ~np.isclose(a, b, rtol=1e-9, atol=1e-12))
        x100 = real & np.isclose(b * 100.0, a, rtol=1e-4, atol=1e-9)
        other = real & ~x100
        print("\n===== %s: x100 / non-x100 breakdown =====" % name)
        print("real=%d x100=%d other=%d"
              % (int(real.sum()), int(x100.sum()), int(other.sum())))
        for tag, m in (("x100", x100), ("other", other)):
            if not m.any():
                continue
            d = ts[m]
            print("  [%s] first=%s last=%s n_dates=%d"
                  % (tag, d.min().date(), d.max().date(),
                     len(np.unique(d))))
            cnt = pd.Series(d).value_counts().sort_index()
            top = cnt.tail(12)
            print("  [%s] last dates: %s"
                  % (tag, {str(k.date()): int(v)
                           for k, v in top.items()}))
        if other.any():
            am, bm = a[other], b[other]
            use = (np.abs(am) > MIN_ABS) & (np.abs(bm) > MIN_ABS)
            if use.any():
                r = am[use] / bm[use]
                print("  [other] D/E ratio p1/p50/p99 = %.4g / %.4g / %.4g"
                      % (float(np.percentile(r, 1)), float(np.median(r)),
                         float(np.percentile(r, 99))))
                print("  [other] sample D vs E:")
                s = np.flatnonzero(other)[:8]
                for i in s:
                    print("    %s D=%-14.6g E=%-14.6g"
                          % (ts[i].date(), a[i], b[i]))
    print("\n===== 2026-08-20 whole-day ratio profile =====")
    day = (ts >= np.datetime64("2026-08-20")) & (ts <= np.datetime64("2026-08-20"))
    print("common rows on 2026-08-20 = %d" % int(day.sum()))
    for name in ("turnover_rate", "turnover_rate_f", "volume_ratio",
                 "circ_mv", "total_mv", "pe_ttm"):
        a = col(D_DAILY, name, sd)[day]
        b = col(E_DAILY, name, se)[day]
        both = ~np.isnan(a) & ~np.isnan(b)
        use = both & (np.abs(a) > MIN_ABS) & (np.abs(b) > MIN_ABS)
        if not use.any():
            print("  %-16s both=%d (no comparable)" % (name, int(both.sum())))
            continue
        r = a[use] / b[use]
        near1 = int(np.isclose(r, 1.0, rtol=1e-6).sum())
        near100 = int(np.isclose(r, 100.0, rtol=1e-4).sum())
        print("  %-16s both=%5d ratio~1=%5d ratio~100=%5d "
              "p1/p50/p99=%.4g/%.4g/%.4g"
              % (name, int(both.sum()), near1, near100,
                 float(np.percentile(r, 1)), float(np.median(r)),
                 float(np.percentile(r, 99))))

    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())