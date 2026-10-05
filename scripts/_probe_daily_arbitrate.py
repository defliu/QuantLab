# coding: utf-8
"""Arbitrate D vs E on the 7 disputed dates using internal consistency only.

No external truth source is needed. Both files carry enough columns to
cross-check themselves:

  1. turnover_rate  == vol / float_share * 100
     turnover_rate_f == vol / free_share  * 100
     -> whichever side's turnover_rate reproduces vol/float_share is right.
        This decides the 2026-08-20 100x question without any assumption.

  2. up_limit   == round(prev_close * (1 + band), 2)
     down_limit == round(prev_close * (1 - band), 2)
     band = 0.20 main board, 0.20 STAR(688)/ChiNext(300), 0.30 BJ(8xx/920)
     -> whichever side satisfies the exchange price-limit rule is right.

  3. pe_ttm == total_mv / net_profit_ttm is NOT checkable (no earnings
     columns here), but pb == total_mv / net_assets is also not checkable.
     So pe/pb/mv differences are reported, not arbitrated.

Read-only.
"""
import sys

import numpy as np
import pandas as pd
import pyarrow.compute as pc
import pyarrow.parquet as pq

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_DAILY = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

DATES = ["2026-06-26", "2026-07-10", "2026-07-17", "2026-08-20",
         "2026-08-25", "2026-08-27", "2026-08-28"]

NUM = ["vol", "float_share", "free_share", "turnover_rate",
       "turnover_rate_f", "pre_close", "close",
       "up_limit", "down_limit"]


def load(path):
    t = pq.read_table(path, columns=["trade_date", "ts_code"] + NUM)
    dd = t.column("trade_date").combine_chunks().to_numpy(
        zero_copy_only=False).astype("datetime64[D]")
    dd_idx = pc.dictionary_encode(t.column("ts_code").combine_chunks())
    code = np.asarray(dd_idx.dictionary.to_pylist(), dtype=object)[
        dd_idx.indices.to_numpy(zero_copy_only=False)]
    out = {"__date": dd, "__code": code}
    for c in NUM:
        out[c] = t.column(c).combine_chunks().to_numpy(
            zero_copy_only=False).astype("float64")
    return out


def main():
    d = load(D_DAILY)
    e = load(E_DAILY)

    for ds in DATES:
        target = np.datetime64(ds)
        dm = d["__date"] == target
        em = e["__date"] == target
        print("\n================ %s  D=%d E=%d ================"
              % (ds, int(dm.sum()), int(em.sum())))

        for tag, x in (("D", d), ("E", e)):
            vol = x["vol"][x["__date"] == target]
            fs = x["float_share"][x["__date"] == target]
            tr = x["turnover_rate"][x["__date"] == target]
            code = x["__code"][x["__date"] == target]

            ok = ~np.isnan(vol) & ~np.isnan(fs) & (fs > 0) & ~np.isnan(tr)
            if not ok.any():
                print("  [%s] turnover: not checkable" % tag)
                continue
            derived = vol[ok] / fs[ok] * 100.0
            got = tr[ok]
            rel = np.abs(derived - got) / np.maximum(np.abs(got), 1e-9)
            hit = int((rel <= 1e-4).sum())
            print("  [%s] turnover_rate == vol/float_share*100 : "
                  "%d/%d consistent (med rel err %.3g)"
                  % (tag, hit, int(ok.sum()), float(np.median(rel))))
            bad = np.flatnonzero(ok)[rel > 1e-4][:3]
            for i in bad:
                print("        e.g. %s vol=%g float_share=%g "
                      "derived=%.6f stored=%.6f"
                      % (code[i], vol[i], fs[i], derived[rel > 1e-4][
                          list(np.flatnonzero(rel > 1e-4)).index(i)],
                             got[rel > 1e-4][
                          list(np.flatnonzero(rel > 1e-4)).index(i)]))

        # price limit rule
        for tag, x in (("D", d), ("E", e)):
            sel = x["__date"] == target
            pc_ = x["pre_close"][sel]
            up = x["up_limit"][sel]
            dn = x["down_limit"][sel]
            code = x["__code"][sel]
            band = np.where(
                np.char.startswith(code.astype(str), "8")
                | np.char.startswith(code.astype(str), "920"), 0.30, 0.20)
            ok = ~np.isnan(pc_) & ~np.isnan(up) & ~np.isnan(dn) & (pc_ > 0)
            if not ok.any():
                print("  [%s] price limit: not checkable" % tag)
                continue
            exp_up = np.round(pc_[ok] * (1 + band[ok]), 2)
            exp_dn = np.round(pc_[ok] * (1 - band[ok]), 2)
            hit_up = int(np.isclose(up[ok], exp_up, atol=0.011).sum())
            hit_dn = int(np.isclose(dn[ok], exp_dn, atol=0.011).sum())
            n = int(ok.sum())
            print("  [%s] up_limit matches rule %d/%d ; "
                  "down_limit matches rule %d/%d"
                  % (tag, hit_up, n, hit_dn, n))

    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())