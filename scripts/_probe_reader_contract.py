# coding: utf-8
"""Probe production reader contract vs real D/E data.

  1. daily parquet: schema, index metadata, sort order, pe/pe_ttm
     -> astock_finance_reader.get_daily_pe uses .iloc[-1]
     -> astock_finance_reader._load_daily requires MultiIndex
  2. fina_indicator ann_date/end_date format mix
     -> astock_finance_reader uses astype(str) compare + idxmax
     -> quantify PIT leak and wrong-quarter selection
  3. (ts_code, end_date) tie groups -> factors/fina.py searchsorted tie-break
"""
import re
import sys

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

SEP = "\u0001"
NA = "<NA>"

D_DAILY = r"D:/astock/daily/stock_daily.parquet"
E_ROOT = r"E:\temp\行情数据"
E_DAILY = E_ROOT + r"\个股行情全量\stock_daily_full.parquet"
D_FIN_DIR = r"D:/astock/finance"
E_FIN_DIR = E_ROOT

FIELDS = ["roe", "fcff", "fcfe", "ocfps"]
RE_DATE_TOKEN = re.compile(r"(?:^|_)(?:date|month|dt|day)(?:_|$)")
RE_DIGITS = re.compile(r"\d+")


def is_date_col(name):
    return bool(RE_DATE_TOKEN.search(str(name).lower()))


def isna_scalar(v):
    try:
        r = pd.isna(v)
    except (TypeError, ValueError):
        return False
    if isinstance(r, (bool, np.bool_)):
        return bool(r)
    return False


def norm_date(v):
    if isna_scalar(v):
        return NA
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y%m%d")
    s = str(v).strip()
    if s.isdigit() and len(s) == 8:
        return s
    digits = RE_DIGITS.findall(s)
    return "".join(digits[:8]) if len(digits) >= 8 else s


def fmt_pattern(series):
    out = {}
    for v in series:
        if isna_scalar(v):
            pat = "<NA>"
        else:
            pat = re.sub(r"\d", "9", str(v))
        out[pat] = out.get(pat, 0) + 1
    return sorted(out.items(), key=lambda kv: -kv[1])[:8]


def pandas_index_columns(path):
    md = pq.ParquetFile(path).schema_arrow.metadata
    if not md or b"pandas" not in md:
        return None
    import json
    j = json.loads(md[b"pandas"].decode("utf-8"))
    return j.get("index_columns")


def probe_daily(tag, path):
    print("\n== DAILY %s ==" % tag)
    sch = pq.ParquetFile(path).schema_arrow
    names = list(sch.names)
    print("cols=%d order_head=%s" % (len(names), names[:6]))
    print("pandas_index_columns=%s" % (pandas_index_columns(path),))
    for c in ("pe", "pe_ttm", "circ_mv", "turnover_rate", "adj_factor",
              "amount", "close"):
        print("  has %-13s %s" % (c, c in names))

    df = pd.read_parquet(path, columns=[])
    idx = df.index
    print("index_type=%s nlevels=%s names=%s" % (
        type(idx).__name__, getattr(idx, "nlevels", 1), list(idx.names)))
    if isinstance(idx, pd.MultiIndex):
        for lv in range(idx.nlevels):
            lev = idx.get_level_values(lv)
            print("  level%d dtype=%s mono_inc=%s" % (
                lv, lev.dtype, lev.is_monotonic_increasing))
        date_v = np.asarray(idx.get_level_values(0))
        code_v = np.asarray(idx.get_level_values(1))
        print("  ts_code descents=%d" % int((code_v[1:] < code_v[:-1]).sum()))
        hd = np.unique(date_v[:2000])
        hc = np.unique(code_v[:2000])
        print("  head2k uniq_dates=%d uniq_codes=%d -> %s" % (
            len(hd), len(hc),
            "date-major" if len(hd) == 1 else "code-major"))
        print("  trade_date min=%s max=%s" % (date_v.min(), date_v.max()))
    del df, idx
    return names, sch


def probe_fina(tag, path):
    print("\n== FINA %s ==" % tag)
    cols = ["ts_code", "end_date", "ann_date"] + FIELDS
    df = pd.read_parquet(path, columns=cols)
    print("rows=%d" % len(df))
    for c in ("end_date", "ann_date"):
        print("  %s dtype=%s patterns=%s" % (c, df[c].dtype,
                                             fmt_pattern(df[c])))
    return df


def pit_leak(d):
    print("\n== PIT LEAK SIM (astock_finance_reader.get_fundamentals_pit) ==")
    d = d.copy()
    d["_ann_str"] = d["ann_date"].astype(str)
    d["_ann_nrm"] = d["ann_date"].map(norm_date)
    d["_end_str"] = d["end_date"].astype(str)
    d["_end_nrm"] = d["end_date"].map(norm_date)
    iso = d["_ann_str"].str.contains("-", regex=False)
    print("ann_date ISO-format rows=%d (%.2f%%)"
          % (int(iso.sum()), 100.0 * iso.mean()))
    print("end_date ISO-format rows=%d (%.2f%%)"
          % (int(d["_end_str"].str.contains("-", regex=False).sum()),
             100.0 * d["_end_str"].str.contains("-", regex=False).mean()))

    asofs = ["20250131", "20250630", "20251231", "20260331", "20260630",
             "20260901"]
    print("\nasof      codes  leaked_rows  wrong_quarter  future_selected")
    tot_leak = tot_wrong = tot_future = 0
    for a in asofs:
        m_str = d["_ann_str"] <= a
        sub = d.loc[m_str]
        if sub.empty:
            continue
        sel = sub.groupby("ts_code", sort=False)["_end_str"].idxmax()
        picked = sub.loc[sel]
        # true latest visible quarter under normalized comparison
        m_true = d["_ann_nrm"] <= a
        true_sub = d.loc[m_true]
        true_best = true_sub.groupby("ts_code", sort=False)["_end_nrm"].max()
        picked_idx = picked.index
        picked_end = d.loc[picked_idx, "_end_nrm"]
        best = true_best.reindex(picked["ts_code"]).to_numpy()
        wrong = int((picked_end.to_numpy() < best).sum())
        future = int((picked["_ann_nrm"] > a).sum())
        leaked = int((m_str & ~m_true).sum())
        tot_leak += leaked
        tot_wrong += wrong
        tot_future += future
        print("%s  %6d  %11d  %13d  %16d"
              % (a, picked["ts_code"].nunique(), leaked, wrong, future))
    print("TOTAL leaked_rows=%d wrong_quarter=%d future_selected=%d"
          % (tot_leak, tot_wrong, tot_future))
    del d


def tie_report(d, e):
    print("\n== TIE ANALYSIS (factors/fina.py searchsorted tie-break) ==")
    store = {}
    for side, df in (("D", d), ("E", e)):
        keys = (df["ts_code"].astype(str) + SEP
                + df["end_date"].map(norm_date))
        g = keys.groupby(keys).size()
        multi = g[g > 1]
        print("%s rows=%d keys=%d tie_groups=%d tie_rows=%d" % (
            side, len(df), len(g), len(multi),
            int(multi.sum()) if len(multi) else 0))
        store[side] = keys

    both = pd.concat([d, e], ignore_index=True, copy=False)
    keys = (both["ts_code"].astype(str) + SEP
            + both["end_date"].map(norm_date))
    scodes, _ = pd.factorize(pd.Series(keys.to_numpy(), dtype=object),
                             sort=False)
    n_groups = int(scodes.max()) + 1
    cnt = np.bincount(scodes, minlength=n_groups)
    tie_groups = np.flatnonzero(cnt > 1)
    print("combined rows=%d keys=%d tie_groups=%d tie_rows=%d" % (
        len(both), n_groups, len(tie_groups), int(cnt[cnt > 1].sum())))

    vals = {}
    for f in FIELDS:
        a = both[f].to_numpy()
        codes, _ = pd.factorize(pd.Series(a, dtype=object),
                                use_na_sentinel=False)
        num = pd.to_numeric(pd.Series(a, dtype=object),
                            errors="coerce").to_numpy(dtype="float64")
        vals[f] = (codes, num, ~np.isnan(num))

    risk_groups = 0
    risk_rows = 0
    for gi in tie_groups:
        rows = np.flatnonzero(scodes == gi)
        diff = False
        for f in FIELDS:
            c, num, nn = vals[f]
            sub_c = c[rows]
            sub_n = num[rows]
            sub_nn = nn[rows]
            if len(np.unique(sub_c)) > 1:
                if not sub_nn.all():
                    diff = True
                    break
                if len(np.unique(sub_n)) > 1:
                    diff = True
                    break
                if len(np.unique(np.round(sub_n, 6))) > 1:
                    diff = True
                    break
        if diff:
            risk_groups += 1
            risk_rows += len(rows)
    print("fina-field divergent tie groups=%d rows=%d" % (risk_groups,
                                                          risk_rows))
    del both, store


def main():
    dnames, dsch = probe_daily("D", D_DAILY)
    enames, esch = probe_daily("E", E_DAILY)
    print("\n== DAILY column diff ==")
    print("order_identical=%s" % (dnames == enames))
    if dnames != enames:
        print("D-only=%s" % [n for n in dnames if n not in set(enames)])
        print("E-only=%s" % [n for n in enames if n not in set(dnames)])
        print("D order=%s" % dnames)
        print("E order=%s" % enames)
    dt = {f.name: str(f.type) for f in dsch}
    et = {f.name: str(f.type) for f in esch}
    diff = [(n, dt.get(n), et.get(n)) for n in dnames
            if n in et and dt.get(n) != et.get(n)]
    print("dtype_diffs=%d %s" % (len(diff), diff[:12]))

    d = probe_fina("D", D_FIN_DIR + r"\fina_indicator.parquet")
    e = probe_fina("E", E_FIN_DIR + r"\fina_indicator.parquet")
    pit_leak(d)
    tie_report(d, e)
    print("\nDONE")


if __name__ == "__main__":
    sys.exit(main())