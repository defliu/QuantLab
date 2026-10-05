# coding: utf-8
"""D vs E daily equivalence probe (guards the full-replace decision).

The daily table is replaced wholesale, so it must be proven that E's full
file carries the SAME price basis / units / index layout as the current
production file. Any systematic difference silently rewrites every backtest.

Checks per sampled window:
  - key coverage (d_only / e_only / both)
  - per-column mismatch count, max abs / rel diff
  - close vs adj_factor relationship (recompute forward-adjusted price)
"""
import sys

import numpy as np
import pandas as pd

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

WINDOWS = [
    ("2015-06", "2015-06-01", "2015-06-30"),
    ("2020-06", "2020-06-01", "2020-06-30"),
    ("2025-06", "2025-06-01", "2025-06-30"),
]

NUM_TOL = 1e-6


def read_side(path, start, end):
    lo = pd.Timestamp(start).to_pydatetime()
    hi = pd.Timestamp(end).to_pydatetime() + pd.Timedelta(days=1)
    df = pd.read_parquet(path, filters=[("trade_date", ">=", lo),
                                        ("trade_date", "<", hi)])
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    return df


def col_mismatch(a, b):
    both_na = a.isna() & b.isna()
    one_na = a.isna() ^ b.isna()
    eq = np.zeros(len(a), dtype=bool)
    m = ~a.isna() & ~b.isna()
    if m.any():
        an = pd.to_numeric(a[m], errors="coerce")
        bn = pd.to_numeric(b[m], errors="coerce")
        num_ok = an.notna() & bn.notna()
        close = np.zeros(int(m.sum()), dtype=bool)
        if num_ok.any():
            av = an[num_ok].to_numpy(dtype="float64")
            bv = bn[num_ok].to_numpy(dtype="float64")
            close[num_ok.to_numpy()] = np.isclose(
                av, bv, rtol=NUM_TOL, atol=1e-8, equal_nan=True)
        nonnum = ~num_ok
        if nonnum.any():
            close[nonnum.to_numpy()] = (
                a[m].to_numpy()[nonnum.to_numpy()].astype(str)
                == b[m].to_numpy()[nonnum.to_numpy()].astype(str))
        eq[np.flatnonzero(m.to_numpy())] = close
    eq |= both_na.to_numpy()
    bad = ~eq & ~one_na.to_numpy()
    return int(bad.sum())


def report_window(tag, start, end):
    print("\n===== WINDOW %s (%s ~ %s) =====" % (tag, start, end))
    d = read_side(D_DAILY, start, end)
    e = read_side(E_DAILY, start, end)
    print("D rows=%d [%s ~ %s] codes=%d"
          % (len(d), d["trade_date"].min().date(), d["trade_date"].max().date(),
             d["ts_code"].nunique()))
    print("E rows=%d [%s ~ %s] codes=%d"
          % (len(e), e["trade_date"].min().date(), e["trade_date"].max().date(),
             e["ts_code"].nunique()))

    cols = [c for c in d.columns if c in set(e.columns)]
    only_d = [c for c in d.columns if c not in set(e.columns)]
    only_e = [c for c in e.columns if c not in set(d.columns)]
    print("cols common=%d D_only=%s E_only=%s" % (len(cols), only_d, only_e))

    m = d.merge(e, on=["trade_date", "ts_code"], how="outer",
                suffixes=("_d", "_e"), indicator=True)
    vc = m["_merge"].value_counts()
    both = int(vc.get("both", 0))
    donly = int(vc.get("left_only", 0))
    eonly = int(vc.get("right_only", 0))
    print("keys both=%d D_only=%d E_only=%d" % (both, donly, eonly))
    if donly:
        ex = m.loc[m["_merge"] == "left_only", ["trade_date", "ts_code"]]
        print("  D_only sample=%s" % ex.head(5).to_dict("records"))
    if eonly:
        ex = m.loc[m["_merge"] == "right_only", ["trade_date", "ts_code"]]
        print("  E_only sample=%s" % ex.head(5).to_dict("records"))

    mm = m.loc[m["_merge"] == "both"]
    print("\ncol                     mismatch   max_abs      max_rel")
    for c in cols:
        if c in ("trade_date", "ts_code"):
            continue
        a = mm[c + "_d"] if (c + "_d") in mm.columns else mm[c]
        b = mm[c + "_e"] if (c + "_e") in mm.columns else mm[c]
        bad = col_mismatch(a, b)
        an = pd.to_numeric(a, errors="coerce").to_numpy(dtype="float64")
        bn = pd.to_numeric(b, errors="coerce").to_numpy(dtype="float64")
        ok = ~np.isnan(an) & ~np.isnan(bn)
        if ok.any():
            diff = np.abs(an[ok] - bn[ok])
            denom = np.maximum(np.abs(an[ok]), 1e-12)
            mx = float(diff.max()) if diff.size else 0.0
            mr = float((diff / denom).max()) if diff.size else 0.0
        else:
            mx = mr = 0.0
        flag = "" if bad == 0 else "  <== DIFF"
        print("%-22s %9d %11.6g %11.6g%s" % (c, bad, mx, mr, flag))

    if "adj_factor" in mm.columns:
        afd = pd.to_numeric(mm["adj_factor_d"], errors="coerce")
        afe = pd.to_numeric(mm["adj_factor_e"], errors="coerce")
        cd = pd.to_numeric(mm["close_d"], errors="coerce")
        ce = pd.to_numeric(mm["close_e"], errors="coerce")
        last_afd = afd.groupby(mm["ts_code"]).transform("max")
        last_afe = afe.groupby(mm["ts_code"]).transform("max")
        fwd_d = cd * (afd / last_afd)
        fwd_e = ce * (afe / last_afe)
        bad_fwd = col_mismatch(fwd_d, fwd_e)
        print("\nforward-adjusted close mismatch=%d (of %d)" % (bad_fwd, len(mm)))
        print("adj_factor max-per-code differs on %d codes"
              % int((afd.groupby(mm['ts_code']).max()
                     != afe.groupby(mm['ts_code']).max()).sum()))
    del d, e, m, mm


def main():
    for tag, s, t in WINDOWS:
        report_window(tag, s, t)
    print("\nDONE")


if __name__ == "__main__":
    sys.exit(main())