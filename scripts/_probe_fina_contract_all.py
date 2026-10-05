# coding: utf-8
"""八张财务表的合并契约探针（只读）。

与 _probe_fina_contract.py 的区别：那张只覆盖 fina_indicator 且残余歧义
靠手工指定 FIELDS（roe/fcff/fcfe/ocfps）判定。本脚本覆盖全部八张表，
残余歧义改用「整行载荷哈希」——不遗漏任何字段，也不需要为每张表手工
维护字段清单。

仍然只读：不写任何生产目录。
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _keynorm import NA, SEP, is_date_col, norm_date, norm_scalar  # noqa: E402

D_DIR = r"D:/astock/finance"
E_DIR = r"E:\temp\行情数据"

# 主键来自历史审计结论；share_change_event 用 change_date/change_reason，
# 无 ann_date/end_date，PIT 段自动跳过。
FIN_TABLES = [
    ("balancesheet", ["ts_code", "end_date", "report_type", "ann_date"]),
    ("cashflow", ["ts_code", "end_date", "report_type", "ann_date"]),
    ("income", ["ts_code", "end_date", "report_type", "ann_date"]),
    ("dividend", ["ts_code", "end_date", "ann_date", "div_proc"]),
    ("express", ["ts_code", "end_date", "ann_date"]),
    ("fina_indicator", ["ts_code", "end_date", "ann_date"]),
    ("forecast", ["ts_code", "end_date", "ann_date", "type"]),
    ("share_change_event", ["ts_code", "change_date", "change_reason"]),
]


def flag_numeric(s):
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")


def load(path, keys, tag):
    import pyarrow.parquet as pq

    avail = list(pq.ParquetFile(path).schema_arrow.names)
    need = [k for k in keys if k in avail]
    missing = [k for k in keys if k not in avail]
    if missing:
        print("  %s MISSING KEY COLS=%s" % (tag, missing))
    df = pd.read_parquet(path, columns=avail)
    df["ts_code"] = df["ts_code"].astype(str)

    keycols = list(need)
    parts = []
    for k in keycols:
        s = df[k].map(norm_date) if is_date_col(k) else df[k].map(norm_scalar)
        parts.append(s.to_numpy())
    kk = parts[0]
    for p in parts[1:]:
        kk = kk + SEP + p
    df["_key"] = kk

    if "end_date" in df.columns:
        df["_end_n"] = df["end_date"].map(norm_date)
    if "ann_date" in df.columns:
        df["_ann_n"] = df["ann_date"].map(norm_date)
    if "ts_code" in df.columns and "_end_n" in df.columns:
        df["_k2"] = df["ts_code"] + SEP + df["_end_n"]

    # 载荷列 = 除主键与 update_flag 外的全部列（存外部，不进 DataFrame）
    skip = set(keycols) | {"update_flag"}
    payload = [c for c in df.columns
               if c not in skip and not c.startswith("_")]
    df.attrs["payload"] = payload
    df["_side"] = tag
    print("  %s rows=%d cols=%d payload_cols=%d"
          % (tag, len(df), len(df.columns), len(payload)))
    return df


def row_hash(df, cols):
    """整行载荷哈希：先归一化再哈希，保证 D/E 口径一致。"""
    if not cols:
        return np.zeros(len(df), dtype="uint64")
    sub = df[cols]
    normed = {}
    for c in cols:
        s = sub[c]
        if is_date_col(c):
            normed[c] = s.map(norm_date)
        elif pd.api.types.is_numeric_dtype(s):
            normed[c] = s.astype("float64").round(10)
        else:
            na = s.isna()
            num = pd.to_numeric(s[~na], errors="coerce")
            if len(num) and not bool(num.isna().any()):
                o = pd.Series(np.nan, index=s.index, dtype="float64")
                o[~na] = num.to_numpy(dtype="float64")
                normed[c] = o
            else:
                normed[c] = s.map(norm_scalar)
    nd = pd.DataFrame(normed, index=sub.index)
    return pd.util.hash_pandas_object(nd, index=False).to_numpy(dtype="uint64")


def resolve_flagmax(df):
    n_rows = len(df)
    codes, _ = pd.factorize(pd.Series(df["_key"].to_numpy(), dtype=object),
                            sort=False)
    n_grp = int(codes.max()) + 1
    cnt = np.bincount(codes, minlength=n_grp)
    dup = cnt > 1

    if "update_flag" in df.columns:
        f = flag_numeric(df["update_flag"])
    else:
        f = np.zeros(n_rows, dtype="float64")
    fk = np.where(np.isnan(f), -np.inf, f)
    gmax = np.full(n_grp, -np.inf, dtype="float64")
    np.maximum.at(gmax, codes, fk)

    is_max = fk == gmax[codes]
    tie_cnt = np.bincount(codes[is_max], minlength=n_grp)
    pos = np.arange(n_rows, dtype=np.int64)
    cand = np.where(is_max, pos, -1)
    best = np.full(n_grp, -1, dtype=np.int64)
    np.maximum.at(best, codes, cand)
    base_of_row = best[codes]
    keep_mask = base_of_row == pos

    stats = {
        "rows": n_rows, "groups": n_grp,
        "dup_groups": int(dup.sum()), "dup_rows": int(cnt[dup].sum()),
        "dropped": int(n_rows - keep_mask.sum()),
        "tie_max_groups": int((tie_cnt[dup] > 1).sum()) if bool(dup.any()) else 0,
        "all_nan_flag_groups": int((gmax == -np.inf).sum()),
    }
    return keep_mask, stats


def residual_ambiguity(df, tag, payload):
    """(ts_code,end_date) 多行组 = 下游 searchsorted 会静默任选的版本。"""
    if "_k2" not in df.columns:
        print("  [%s] no end_date -> skipped" % tag)
        return {"multi_groups": 0, "div_groups": 0, "div_rows": 0}
    size = df.groupby("_k2", sort=False).size()
    multi = size[size > 1]
    print("  [%s] k2_groups=%d multi=%d rows_in_multi=%d"
          % (tag, len(size), len(multi), int(multi.sum())))
    if len(multi) == 0:
        return {"multi_groups": 0, "div_groups": 0, "div_rows": 0}
    sub = df[df["_k2"].isin(set(multi.index))]
    h = row_hash(sub, payload)
    nun = pd.Series(h).groupby(sub["_k2"].to_numpy(), sort=False).nunique()
    d = nun[nun > 1]
    return {"multi_groups": int(len(multi)),
            "div_groups": int(len(d)),
            "div_rows": int(multi.reindex(d.index).sum())}


def pit_leak_sim(df, tag, asofs):
    if "_ann_n" not in df.columns or "_end_n" not in df.columns:
        print("  [%s] no ann_date/end_date -> PIT skipped" % tag)
        return None
    d = df.copy()
    d["_ann_s"] = d["ann_date"].astype("string").fillna("nan")
    d["_end_s"] = d["end_date"].astype("string").fillna("nan")
    iso = d["_ann_s"].str.contains("-", regex=False)
    print("  [%s] ann ISO rows=%d (%.2f%%) | ann NA rows=%d"
          % (tag, int(iso.sum()), 100.0 * float(iso.mean()),
             int((d["_ann_n"] == NA).sum())))

    d = d.sort_values(["ts_code", "_end_s"], ascending=[True, False],
                      kind="stable")
    ann_s = d["_ann_s"].to_numpy()
    ann_n = d["_ann_n"].to_numpy()
    tot = dict(leak=0, ahead=0, stale=0, no_true=0, points=0)
    for a in asofs:
        m_str = ann_s <= a
        m_true = ann_n <= a
        leak = int((m_str & ~m_true).sum())
        vis = d.loc[m_str]
        if len(vis) == 0:
            continue
        picked = vis.drop_duplicates("ts_code")
        tv = d.loc[m_true]
        tv_ok = tv["_end_n"] != NA
        best_map = (tv.loc[tv_ok].groupby("ts_code", sort=False)["_end_n"]
                    .max())
        pv = picked["_end_n"].to_numpy()
        bestv = (picked["ts_code"].map(best_map).astype("string")
                 .fillna(NA).to_numpy())
        no_true = int((bestv == NA).sum())
        ok = bestv != NA
        ahead = int((pv[ok] > bestv[ok]).sum())
        stale = int((pv[ok] < bestv[ok]).sum())
        tot["leak"] += leak
        tot["ahead"] += ahead
        tot["stale"] += stale
        tot["no_true"] += no_true
        if leak or ahead or stale or no_true:
            tot["points"] += 1
    print("  [%s] sweep %d pts: leak=%d ahead=%d stale=%d no_true=%d "
          "issue_pts=%d" % (tag, len(asofs), tot["leak"], tot["ahead"],
                            tot["stale"], tot["no_true"], tot["points"]))
    return tot


def probe_table(name, keys):
    t0 = time.time()
    print("\n" + "=" * 70)
    print("TABLE %s  keys=%s" % (name, keys))
    print("=" * 70)
    dp = os.path.join(D_DIR, name + ".parquet")
    ep = os.path.join(E_DIR, name + ".parquet")
    d = load(dp, keys, "D")
    e = load(ep, keys, "E")

    print(" -- 1. KEY SPACE --")
    dk = set(d["_key"].unique().tolist())
    ek = set(e["_key"].unique().tolist())
    print("D keys=%d E keys=%d both=%d D_only=%d E_only=%d"
          % (len(dk), len(ek), len(dk & ek), len(dk - ek), len(ek - dk)))

    print(" -- 2. INTERNAL DEDUP (max update_flag) --")
    keep_d, st_d = resolve_flagmax(d)
    keep_e, st_e = resolve_flagmax(e)
    for tag, st in (("D", st_d), ("E", st_e)):
        print("  %s rows=%d groups=%d dup_groups=%d dup_rows=%d dropped=%d "
              "tie_max=%d all_nan_flag=%d"
              % (tag, st["rows"], st["groups"], st["dup_groups"],
                 st["dup_rows"], st["dropped"], st["tie_max_groups"],
                 st["all_nan_flag_groups"]))

    print(" -- 3. SIMULATED MERGE (E wins, D-only kept) --")
    e_keys = set(e.loc[keep_e, "_key"].tolist())
    d_only = keep_d & ~d["_key"].isin(e_keys)
    merged = pd.concat([e.loc[keep_e], d.loc[d_only]], ignore_index=True)
    pay_d = d.attrs["payload"]
    pay_e = e.attrs["payload"]
    print("merged rows=%d (E_kept=%d D_only=%d) uniq=%d dup=%d"
          % (len(merged), int(keep_e.sum()), int(d_only.sum()),
             merged["_key"].nunique(), len(merged) - merged["_key"].nunique()))
    print("E overwrites D same-key rows=%d"
          % int(e.loc[keep_e, "_key"].isin(dk).sum()))

    print(" -- 4. RESIDUAL AMBIGUITY (payload-hash based) --")
    # merged 是两侧 concat，列集可能不同；只对双方共有的载荷列算哈希，
    # 否则单侧独有列会被当成 NA 参与比较，凭空造出「差异」。
    common_payload = [c for c in pay_d if c in pay_e]
    print(" payload cols: D=%d E=%d common=%d (D-only=%s E-only=%s)"
          % (len(pay_d), len(pay_e), len(common_payload),
             sorted(set(pay_d) - set(common_payload))[:5],
             sorted(set(pay_e) - set(common_payload))[:5]))
    residual_ambiguity(d, "D-raw", pay_d)
    residual_ambiguity(d.loc[keep_d], "D-dedup", pay_d)
    residual_ambiguity(e.loc[keep_e], "E-dedup", pay_e)
    rm = residual_ambiguity(merged, "merged", common_payload)

    print(" -- 5. PIT LEAK SIM --")
    asofs = ["%d%02d%02d" % (y, m, dd)
             for y in range(2018, 2027)
             for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31))]
    pit_d = pit_leak_sim(d, "D-raw", asofs)
    pit_m = pit_leak_sim(merged, "merged", asofs)

    print(" -- SUMMARY %s --" % name)
    print("D_only=%d E_only=%d | tie_max D=%d E=%d | merged_div groups=%d "
          "rows=%d" % (len(dk - ek), len(ek - dk), st_d["tie_max_groups"],
                       st_e["tie_max_groups"], rm["div_groups"],
                       rm["div_rows"]))
    if pit_d and pit_m:
        print("PIT D(leak=%d ahead=%d stale=%d) -> MERGED(leak=%d ahead=%d "
              "stale=%d)" % (pit_d["leak"], pit_d["ahead"], pit_d["stale"],
                              pit_m["leak"], pit_m["ahead"], pit_m["stale"]))
    print("elapsed=%.1fs" % (time.time() - t0))
    return {"name": name, "d_only": len(dk - ek), "e_only": len(ek - dk),
            "tie_d": st_d["tie_max_groups"], "tie_e": st_e["tie_max_groups"],
            "div_groups": rm["div_groups"], "div_rows": rm["div_rows"]}


def main():
    print("FINANCE MERGE CONTRACT PROBE (read-only)")
    rows = []
    failures = []
    for name, keys in FIN_TABLES:
        try:
            rows.append(probe_table(name, keys))
        except Exception as exc:
            print("  !! %s FAILED: %r" % (name, exc))
            import traceback
            traceback.print_exc()
            failures.append(name)

    print("\n" + "=" * 70)
    print("ALL-TABLE SUMMARY")
    print("=" * 70)
    print("%-20s %10s %10s %9s %9s %11s %11s"
          % ("table", "D_only", "E_only", "tieD", "tieE", "divGroups",
             "divRows"))
    for r in rows:
        print("%-20s %10d %10d %9d %9d %11d %11d"
              % (r["name"], r["d_only"], r["e_only"], r["tie_d"], r["tie_e"],
                 r["div_groups"], r["div_rows"]))

    # 覆盖度门禁：声明了 N 张表就必须跑出 N 行结果，缺一即为失败。
    # 缺表而静默返回 0 会让「表根本没读到」被误读成「这张表没问题」。
    missing = [n for n, _ in FIN_TABLES if n not in {r["name"] for r in rows}]
    if missing:
        failures.extend(missing)
        print("MISSING_TABLES=%s" % ",".join(missing))

    if failures:
        print("GATE_FAILED=%s" % ",".join(sorted(set(failures))))
        return 1
    print("GATE_PASS tables=%d" % len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())