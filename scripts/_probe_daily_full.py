# coding: utf-8
"""Full-file D/E daily equivalence gate.

Window sampling is not enough before a wholesale replace: this table is the
price basis of every factor and backtest. This probe verifies, over ALL rows:

  1. key coverage      - E must be a superset of D (D_only must be 0)
  2. key uniqueness    - (trade_date, ts_code) unique on both sides
  3. payload equality  - all 33 columns, bit-exact after key alignment
  4. forward-adjusted  - close * adj_factor / max(adj_factor per code)
  5. physical layout   - row order sorted by (date,code) or (code,date)

Read-only. Touches nothing under D:/astock.
"""
import gc
import sys

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

STR_COLS = ["suspend_timing", "suspend_type"]
KEY_DAYS_MIN = 10000


def die(msg):
    print("FATAL: %s" % msg, flush=True)
    sys.exit(2)


def read_keys(path):
    t = pq.read_table(path, columns=["trade_date", "ts_code"])
    dd = pc.dictionary_encode(t.column("ts_code").combine_chunks())
    uniq = dd.dictionary.to_pylist()
    gid = dd.indices.to_numpy(zero_copy_only=False).astype(np.int32)
    dv = t.column("trade_date").combine_chunks().to_numpy(
        zero_copy_only=False).astype("datetime64[D]")
    dates = dv.astype(np.int64)
    del t, dd, dv
    return dates, gid, uniq


def layout_report(tag, dates, gid):
    nondec = bool(np.all(np.diff(dates) >= 0)) if len(dates) > 1 else True
    ok_dc = bool(np.all((dates[1:] > dates[:-1]) |
                        ((dates[1:] == dates[:-1]) & (gid[1:] >= gid[:-1]))))
    ok_cd = bool(np.all((gid[1:] > gid[:-1]) |
                        ((gid[1:] == gid[:-1]) & (dates[1:] >= dates[:-1]))))
    desc_d = int((np.diff(dates) < 0).sum())
    desc_c = int((np.diff(gid) < 0).sum())
    print("[%s] rows=%d date_nondecreasing=%s date_descents=%d "
          "code_descents=%d lexsorted(date,code)=%s lexsorted(code,date)=%s"
          % (tag, len(dates), nondec, desc_d, desc_c, ok_dc, ok_cd),
          flush=True)


def main():
    print("=" * 70)
    print("PHASE 1  keys / coverage / uniqueness")
    print("=" * 70)

    dd_dates, dd_gid, dd_uniq = read_keys(D_DAILY)
    de_dates, de_gid, de_uniq = read_keys(E_DAILY)
    print("D rows=%d codes=%d [%s ~ %s]"
          % (len(dd_dates), len(dd_uniq),
             str(pd_date(dd_dates.min())), str(pd_date(dd_dates.max()))))
    print("E rows=%d codes=%d [%s ~ %s]"
          % (len(de_dates), len(de_uniq),
             str(pd_date(de_dates.min())), str(pd_date(de_dates.max()))))

    layout_report("D", dd_dates, dd_gid)
    layout_report("E", de_dates, de_gid)

    all_codes = sorted(set(dd_uniq) | set(de_uniq))
    n_codes = len(all_codes)
    pos = {c: i for i, c in enumerate(all_codes)}
    print("union codes=%d  D_only_codes=%d  E_only_codes=%d"
          % (n_codes,
             len(set(dd_uniq) - set(de_uniq)),
             len(set(de_uniq) - set(dd_uniq))))
    for c in sorted(set(de_uniq) - set(dd_uniq)):
        print("  code only in E: %s" % c)

    dgid = np.fromiter((pos[c] for c in dd_uniq), dtype=np.int32,
                       count=len(dd_uniq))[dd_gid]
    egid = np.fromiter((pos[c] for c in de_uniq), dtype=np.int32,
                       count=len(de_uniq))[de_gid]
    del dd_gid, de_gid

    mult = np.int64(max(n_codes, KEY_DAYS_MIN))
    dkey = dd_dates.astype(np.int64) * mult + dgid.astype(np.int64)
    ekey = de_dates.astype(np.int64) * mult + egid.astype(np.int64)

    dsort = np.sort(dkey)
    esort = np.sort(ekey)
    print("D dup keys=%d" % int((np.diff(dsort) == 0).sum()))
    print("E dup keys=%d" % int((np.diff(esort) == 0).sum()))

    def missing_in(sort_other, sort_self):
        idx = np.searchsorted(sort_other, sort_self)
        np.clip(idx, 0, len(sort_other) - 1, out=idx)
        return int((sort_other[idx] != sort_self).sum())

    n_d_only = missing_in(esort, dsort)
    n_e_only = missing_in(dsort, esort)
    print("KEY COVERAGE  D_only=%d  E_only=%d" % (n_d_only, n_e_only))
    if n_d_only:
        bad = dsort[esort[np.searchsorted(esort, dsort)] != dsort]
        for k in bad[:10]:
            days = int(k // mult)
            print("  D_only key date=%s code=%s"
                  % (pd_date(days), all_codes[int(k % mult)]))

    lo = int(min(dd_dates.min(), de_dates.min()))
    hi = int(max(dd_dates.max(), de_dates.max()))
    cnt_d = np.bincount(dd_dates - lo, minlength=hi - lo + 1)
    cnt_e = np.bincount(de_dates - lo, minlength=hi - lo + 1)
    bad_days = np.flatnonzero(cnt_d != cnt_e)
    print("dates with row-count mismatch=%d" % len(bad_days))
    for k in bad_days[:10]:
        print("  %s  D=%d E=%d" % (pd_date(lo + k), cnt_d[k], cnt_e[k]))

    rc_d = np.bincount(dgid, minlength=n_codes)
    rc_e = np.bincount(egid, minlength=n_codes)
    bad_codes = np.flatnonzero(rc_d != rc_e)
    print("codes with row-count mismatch=%d" % len(bad_codes))
    for k in bad_codes[:10]:
        print("  %s  D=%d E=%d" % (all_codes[k], rc_d[k], rc_e[k]))
    del cnt_d, cnt_e, rc_d, rc_e, bad_days, bad_codes

    print("\nordering rows by key for payload comparison...", flush=True)
    order_d = np.argsort(dkey, kind="stable")
    order_e = np.argsort(ekey, kind="stable")
    dkey_s = dkey[order_d]
    ekey_s = ekey[order_e]
    del dkey, ekey, dsort, esort

    probe = np.searchsorted(ekey_s, dkey_s)
    np.clip(probe, 0, len(ekey_s) - 1, out=probe)
    in_common = ekey_s[probe] == dkey_s
    pos_d = np.flatnonzero(in_common)
    pos_e = probe[in_common]
    n_common = len(pos_d)
    print("common keys=%d (aligned on identical key sequence=%s)"
          % (n_common, bool(np.array_equal(dkey_s[pos_d], ekey_s[pos_e]))))
    aligned_days = dkey_s[pos_d] // mult
    sel_d = order_d[pos_d]
    sel_e = order_e[pos_e]
    gd = dgid[sel_d]
    del dkey_s, ekey_s, probe, in_common, pos_d, pos_e, order_d, order_e

    if n_common != len(dd_dates) - n_d_only:
        die("common key count inconsistent with coverage report")

    print("\n" + "=" * 70)
    print("PHASE 2  payload equality, all columns, key-aligned")
    print("=" * 70)

    fd = pq.ParquetFile(D_DAILY)
    fe = pq.ParquetFile(E_DAILY)
    num_cols = [f.name for f in fd.schema_arrow
                if f.name not in ("trade_date", "ts_code")
                and str(f.type) == "double"]
    print("numeric cols=%d  string cols=%s" % (len(num_cols), STR_COLS))

    worst = []
    for c in num_cols:
        a = fd.read(columns=[c]).column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")[sel_d]
        b = fe.read(columns=[c]).column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")[sel_e]
        na, nb = np.isnan(a), np.isnan(b)
        bad = na ^ nb
        m = ~na & ~nb
        if m.any():
            bad |= m & (a != b)
        n = int(bad.sum())
        if n:
            for i in np.flatnonzero(bad)[:3]:
                worst.append((c, str(pd_date(aligned_days[i])),
                              all_codes[int(gd[i])],
                              float(a[i]), float(b[i])))
        print("  %-16s mismatch=%d" % (c, n), flush=True)
        del a, b, na, nb, bad, m
        gc.collect()

    for c in STR_COLS:
        aa = fd.read(columns=[c]).column(c).combine_chunks()
        bb = fe.read(columns=[c]).column(c).combine_chunks()
        ta = pc.take(aa, pa.array(sel_d, type=pa.int64()))
        tb = pc.take(bb, pa.array(sel_e, type=pa.int64()))
        both_null = pc.and_(pc.is_null(ta), pc.is_null(tb))
        one_null = pc.xor(pc.is_null(ta), pc.is_null(tb))
        eq = pc.or_(pc.fill_null(pc.equal(ta, tb), False), both_null)
        diff = pc.and_(pc.invert(eq), pc.invert(one_null))
        n = pc.sum(pc.cast(diff, pa.int64())).as_py()
        n_one = pc.sum(pc.cast(one_null, pa.int64())).as_py()
        print("  %-16s mismatch=%d one_side_null=%d" % (c, n, n_one),
              flush=True)
        if n:
            for i in pc.indices_nonzero(diff).to_pylist()[:3]:
                worst.append((c, str(pd_date(aligned_days[i])),
                              all_codes[int(gd[i])],
                              str(ta[i].as_py()), str(tb[i].as_py())))
        del aa, bb, ta, tb
        gc.collect()

    if worst:
        print("\nMISMATCH SAMPLES")
        for w in worst:
            print("  %s %s %s  D=%r E=%r" % w)

    print("\n" + "=" * 70)
    print("PHASE 3  forward-adjusted close (production formula)")
    print("=" * 70)
    af_d = fd.read(columns=["adj_factor"]).column("adj_factor")\
        .combine_chunks().to_numpy(zero_copy_only=False)\
        .astype("float64")[sel_d]
    af_e = fe.read(columns=["adj_factor"]).column("adj_factor")\
        .combine_chunks().to_numpy(zero_copy_only=False)\
        .astype("float64")[sel_e]
    cl_d = fd.read(columns=["close"]).column("close").combine_chunks()\
        .to_numpy(zero_copy_only=False).astype("float64")[sel_d]
    cl_e = fe.read(columns=["close"]).column("close").combine_chunks()\
        .to_numpy(zero_copy_only=False).astype("float64")[sel_e]
    mx_d = np.zeros(n_codes)
    mx_e = np.zeros(n_codes)
    np.maximum.at(mx_d, gd, af_d)
    np.maximum.at(mx_e, gd, af_e)
    print("per-code max adj_factor differs on %d codes"
          % int((mx_d != mx_e).sum()))
    fwd_d = cl_d * (af_d / mx_d[gd])
    fwd_e = cl_e * (af_e / mx_e[gd])
    fn = int((~np.isclose(fwd_d, fwd_e, rtol=1e-12, atol=1e-12,
                          equal_nan=True)).sum())
    print("forward-adjusted close mismatch=%d of %d" % (fn, n_common))
    print("\nDONE")
    return 0


def pd_date(days):
    import pandas as pd
    return (pd.Timestamp("1970-01-01") + pd.Timedelta(days=int(days))).date()


if __name__ == "__main__":
    sys.exit(main())