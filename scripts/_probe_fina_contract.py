# coding: utf-8
"""fina_indicator merge-contract probe (read-only).

回答合并契约必须敲定的四件事：
  1. 键空间：E 是否覆盖 D 的键？合并会不会丢 D 独有行？
  2. 内部去重：按 (ts_code, end_date, ann_date) 分组后，max(update_flag)
     是否唯一？若不唯一，去重规则本身就是不确定的（必须显式承认）。
  3. 去重后残余歧义：factors/fina.py 只按 end_date 做 searchsorted，
     同一 (ts_code, end_date) 留下多行 = 静默任选版本。
  4. PIT 泄漏：astock_finance_reader 用 astype(str) 混合格式比较，
     ISO 日期('-' < '0')会被判成「全年可见」——量化这个偏差的方向与幅度。

只读：本脚本不写任何生产目录。
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _keynorm import NA, SEP, norm_date, norm_scalar  # noqa: E402

D_FINA = r"D:/astock/finance/fina_indicator.parquet"
E_FINA = r"E:\temp\行情数据\fina_indicator.parquet"

FIELDS = ["roe", "fcff", "fcfe", "ocfps"]
KEY3 = ["ts_code", "end_date", "ann_date"]


def flag_numeric(s):
    """update_flag -> float64；不可解析/缺失 -> NaN。"""
    return pd.to_numeric(s, errors="coerce").to_numpy(dtype="float64")


def load(path, tag):
    import pyarrow.parquet as pq

    avail = set(pq.ParquetFile(path).schema_arrow.names)
    cols = [c for c in (["ts_code", "end_date", "ann_date", "update_flag"]
                        + FIELDS) if c in avail]
    missing = [c for c in (["ts_code", "end_date", "ann_date", "update_flag"]
                           + FIELDS) if c not in avail]
    print("%s load: rows_cols=%s missing=%s" % (tag, cols, missing))
    df = pd.read_parquet(path, columns=cols)
    df["ts_code"] = df["ts_code"].astype(str)
    df["_end_n"] = df["end_date"].map(norm_date)
    df["_ann_n"] = df["ann_date"].map(norm_date)
    df["_k3"] = df["ts_code"] + SEP + df["_end_n"] + SEP + df["_ann_n"]
    df["_k2"] = df["ts_code"] + SEP + df["_end_n"]
    df["_side"] = tag
    return df


def resolve_flagmax(df):
    """每组恰好保留一行：max(update_flag)，并列时取组内物理最后一行。

    返回 (base_of_row, keep_mask, stats)
      base_of_row[i] = i 所在组被保留行的行号
      keep_mask      = 被保留行的 bool 掩码
    """
    n_rows = len(df)
    codes, _ = pd.factorize(pd.Series(df["_k3"].to_numpy(), dtype=object),
                            sort=False)
    n_grp = int(codes.max()) + 1
    cnt = np.bincount(codes, minlength=n_grp)
    dup = cnt > 1

    f = flag_numeric(df["update_flag"])
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
        "rows": n_rows,
        "groups": n_grp,
        "dup_groups": int(dup.sum()),
        "dup_rows": int(cnt[dup].sum()),
        "dropped": int(n_rows - keep_mask.sum()),
        "tie_max_groups": int((tie_cnt[dup] > 1).sum()),
        "all_nan_flag_groups": int((gmax == -np.inf).sum()),
    }
    print("  %s rows=%d groups=%d dup_groups=%d dup_rows=%d dropped=%d"
          % (df["_side"].iloc[0], stats["rows"], stats["groups"],
             stats["dup_groups"], stats["dup_rows"], stats["dropped"]))
    print("    groups with NON-UNIQUE max flag = %d  <- 去重规则不确定性"
          % stats["tie_max_groups"])
    print("    groups where all update_flag unparsable/NA = %d"
          % stats["all_nan_flag_groups"])
    return base_of_row, keep_mask, stats


def _normalized_values(s):
    """把一列转成可比较的数值/字符串数组（NA 保持缺失语义）。"""
    if pd.api.types.is_numeric_dtype(s):
        return s.astype("float64")
    na = s.isna()
    num = pd.to_numeric(s[~na], errors="coerce")
    if len(num) and not bool(num.isna().any()):
        out = pd.Series(np.nan, index=s.index, dtype="float64")
        out[~na] = num.to_numpy(dtype="float64")
        return out
    return s.map(norm_scalar)


def _distinct_per_group(sub, kcol, col):
    """组内该列的 distinct 值个数（NaN 视作一个独立取值）。"""
    tmp = sub[[kcol]].copy()
    tmp["_v"] = _normalized_values(sub[col]).to_numpy()
    if tmp["_v"].dtype.kind == "f":
        tmp["_v"] = tmp["_v"].round(10)
    return tmp.groupby(kcol, sort=False)["_v"].nunique(dropna=False)


def residual_ambiguity(df, tag):
    """(ts_code, end_date) 多行组：fields/fina.py 会静默任选版本。"""
    size = df.groupby("_k2", sort=False).size()
    multi = size[size > 1]
    print("  [%s] (ts_code,end_date) groups=%d multi=%d rows_in_multi=%d"
          % (tag, len(size), len(multi), int(multi.sum())))
    if len(multi) == 0:
        return {"multi_groups": 0, "div_groups": 0, "div_rows": 0}
    sub = df[df["_k2"].isin(set(multi.index))]
    div_idx = set()
    per_field = {}
    for f in FIELDS:
        if f not in sub.columns:
            continue
        nun = _distinct_per_group(sub, "_k2", f)
        d = nun[nun > 1]
        per_field[f] = int(len(d))
        div_idx.update(d.index.tolist())
    for f, v in per_field.items():
        print("    field %-10s divergent_groups=%d" % (f, v))
    div_rows = int(size.reindex(list(div_idx)).sum()) if div_idx else 0
    union = len(div_idx)
    print("    UNION divergent_groups=%d rows=%d  (取并集，非按字段累加)"
          % (union, div_rows))
    return {"multi_groups": int(len(multi)),
            "div_groups": union,
            "div_rows": div_rows}


def pit_leak_sim(df, tag, asofs):
    """模拟 astock_finance_reader 的当前实现，量化字符串比较的偏差。

    reader 实际逻辑：
        ann = sub['ann_date'].astype(str); mask = ann <= asof
        row = visible.loc[visible['end_date'].astype(str).idxmax()]

    排序一次即可复用：组内按 end_date 字符串降序，取首个可见行 == idxmax
    在「只保留可见行」后的结果（子集不改变相对次序）。
    """
    d = df.copy()
    d["_ann_s"] = d["ann_date"].astype("string").fillna("nan")
    d["_end_s"] = d["end_date"].astype("string").fillna("nan")
    iso = d["_ann_s"].str.contains("-", regex=False)
    print("  [%s] ann_date ISO-format rows=%d (%.2f%%)  end_date ISO rows=%d"
          % (tag, int(iso.sum()), 100.0 * float(iso.mean()),
             int(d["_end_s"].str.contains("-", regex=False).sum())))
    if int(iso.sum()):
        yrs = sorted(set(d.loc[iso, "_ann_n"].str[:4].tolist()))
        print("    ISO ann_date years=%s" % yrs)

    d = d.sort_values(["ts_code", "_end_s"], ascending=[True, False],
                      kind="stable")
    ann_s = d["_ann_s"].to_numpy()
    ann_n = d["_ann_n"].to_numpy()
    nan_ann = ann_n == NA
    print("    ann_date unparsable/NA rows=%d" % int(nan_ann.sum()))

    tot = dict(leak=0, ahead=0, stale=0, no_true=0, codes=0,
               points_with_issue=0)
    worst = []
    for a in asofs:
        m_str = ann_s <= a
        m_true = ann_n <= a
        leak = int((m_str & ~m_true).sum())
        vis = d.loc[m_str]
        if len(vis) == 0:
            continue
        picked = vis.drop_duplicates("ts_code")
        true_vis = d.loc[m_true]
        # end_date 归一化为 NA 的行不能参与「最新报告期」评选：
        # 字符串 max() 会把 '<NA>' 排到所有 YYYYMMDD 之后，等于伪最新。
        tv_ok = true_vis["_end_n"] != NA
        best_map = (true_vis.loc[tv_ok].groupby("ts_code", sort=False)["_end_n"]
                    .max())
        pv = picked["_end_n"].to_numpy()
        # map 对不存在的键给 float NaN，需与字符串 NA 一起归一后再比较
        bestv = (picked["ts_code"].map(best_map)
                 .astype("string").fillna(NA).to_numpy())
        # 归一化后完全不可见的股票：reader 仍凭空造出一行（最严重的一类）
        no_true = int((bestv == NA).sum())
        ok = bestv != NA
        ahead = int((pv[ok] > bestv[ok]).sum())
        stale = int((pv[ok] < bestv[ok]).sum())
        tot["leak"] += leak
        tot["ahead"] += ahead
        tot["stale"] += stale
        tot["no_true"] += no_true
        tot["codes"] += int(len(picked))
        if leak or ahead or stale or no_true:
            tot["points_with_issue"] += 1
            worst.append((a, leak, ahead, stale, no_true, len(picked)))
    print("    asof sweep %d points: leaked_rows=%d | picked_FUTURE_quarter=%d"
          " | picked_STALE_quarter=%d | codes_with_NO_true_row=%d"
          " | codes_touched=%d"
          % (len(asofs), tot["leak"], tot["ahead"], tot["stale"],
             tot["no_true"], tot["codes"]))
    print("    points with any issue = %d" % tot["points_with_issue"])
    for a, l, ah, st, nt, nc in worst[:8]:
        print("      asof=%s leak=%d ahead=%d stale=%d no_true=%d codes=%d"
              % (a, l, ah, st, nt, nc))
    return tot


def main():
    d = load(D_FINA, "D")
    e = load(E_FINA, "E")

    print("\n== 1. KEY SPACE (normalized ts_code+end_date+ann_date) ==")
    dk = pd.Index(d["_k3"].unique())
    ek = pd.Index(e["_k3"].unique())
    both = dk.intersection(ek)
    print("D k3=%d  E k3=%d  both=%d  D_only=%d  E_only=%d"
          % (len(dk), len(ek), len(both), len(dk.difference(ek)),
             len(ek.difference(dk))))
    print("D rows=%d  E rows=%d  E/D row ratio=%.2f"
          % (len(d), len(e), len(e) / max(len(d), 1)))

    print("\n== 2. INTERNAL DEDUP (max update_flag) ==")
    print(" D:")
    base_d, keep_d, st_d = resolve_flagmax(d)
    print(" E:")
    base_e, keep_e, st_e = resolve_flagmax(e)

    print("\n== 3. SIMULATED MERGE (E wins on k3, D-only kept) ==")
    e_keys = set(e.loc[keep_e, "_k3"].tolist())
    d_only_mask = keep_d & ~d["_k3"].isin(e_keys)
    d_keep = d.loc[d_only_mask]
    merged = pd.concat([e.loc[keep_e], d_keep], ignore_index=True)
    print("merged rows=%d (E_kept=%d + D_only_kept=%d)"
          % (len(merged), int(keep_e.sum()), int(d_only_mask.sum())))
    print("merged unique k3=%d  dup=%d"
          % (merged["_k3"].nunique(),
             len(merged) - merged["_k3"].nunique()))
    e_kept_k3 = e.loc[keep_e, "_k3"]
    clash = int(e_kept_k3.isin(set(dk)).sum())
    print("E rows kept that OVERWRITE a D row on same k3 = %d" % clash)
    print("D rows dropped because E has same k3 = %d"
          % int((keep_d & d["_k3"].isin(e_keys)).sum()))

    print("\n== 4. RESIDUAL AMBIGUITY on (ts_code,end_date) ==")
    print(" D (current production, before dedup):")
    residual_ambiguity(d, "D-raw")
    print(" D after flagmax dedup:")
    residual_ambiguity(d.loc[keep_d], "D-dedup")
    print(" E after flagmax dedup:")
    residual_ambiguity(e.loc[keep_e], "E-dedup")
    print(" MERGED:")
    res_m = residual_ambiguity(merged, "merged")

    print("\n== 5. PIT LEAK SIM (astock_finance_reader current impl) ==")
    asofs = ["%d%02d%02d" % (y, m, dd)
             for y in range(2018, 2027)
             for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31))]
    print(" D (current production):")
    pit_leak_sim(d, "D-raw", asofs)
    print(" MERGED (simulated):")
    pit_leak_sim(merged, "merged", asofs)

    print("\n== CONTRACT SUMMARY ==")
    print("k3 D_only=%d E_only=%d" % (len(dk.difference(ek)),
                                       len(ek.difference(dk))))
    print("dedup tie_max_groups: D=%d E=%d" % (st_d["tie_max_groups"],
                                                st_e["tie_max_groups"]))
    print("merged residual divergent (ts_code,end_date) groups=%d rows=%d"
          % (res_m["div_groups"], res_m["div_rows"]))
    print("PROBE_DONE")


if __name__ == "__main__":
    sys.exit(main())
