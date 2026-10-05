# coding: utf-8
"""财务表残余歧义下钻（只读）。

针对八表探针留下的三个悬而未决点，做定点排查：

  Q1  fina_indicator 的 2 个、forecast 的 414 个 D-only 键到底是什么？
      ISO 归一化后仍真实不同，还是主键某一分量（ann_date / type）差异？
      如果 D-only 行其实在 E 侧有同 (ts_code,end_date) 的行、只是
      ann_date 更晚，那它是「E 尚未覆盖」而非「E 缺数据」，性质完全不同。

  Q2  dividend 合并后残余的 194 条 PIT 泄漏来自哪里？
      已知 leak 判据是 `ann_date.astype(str) <= asof`：
      ISO 串 '2026-12-31' 与 '20260630' 在第 5 位比较 '-' < '0'，
      会让当年后续公告全部误判为可见。需要确认合并后到底是哪些行、
      属于 D 还是 E、ann_date 原始形态是什么。

  Q3  dividend 合并后 no_true=11：无「真实可见行」的股票，
      是 E 侧 ann_date 整体偏晚，还是这些票根本没有分红记录？

只读，不写生产目录。
"""
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _keynorm import NA, SEP, is_date_col, norm_date, norm_scalar  # noqa: E402

D_DIR = r"D:/astock/finance"
E_DIR = r"E:\temp\行情数据"

TARGETS = {
    "fina_indicator": ["ts_code", "end_date", "ann_date"],
    "forecast": ["ts_code", "end_date", "ann_date", "type"],
    "dividend": ["ts_code", "end_date", "ann_date", "div_proc"],
}


def load(path, keys, tag):
    df = pd.read_parquet(path)
    df["ts_code"] = df["ts_code"].astype(str)
    parts = []
    for k in keys:
        s = df[k].map(norm_date) if is_date_col(k) else df[k].map(norm_scalar)
        parts.append(s.to_numpy())
    kk = parts[0]
    for p in parts[1:]:
        kk = kk + SEP + p
    df["_key"] = kk
    df["_side"] = tag
    if "end_date" in df.columns:
        df["_end_n"] = df["end_date"].map(norm_date)
    if "ann_date" in df.columns:
        df["_ann_n"] = df["ann_date"].map(norm_date)
        # ann_date 原始形态普查：决定字符串比较是否安全
        raw = df["ann_date"].astype("string").fillna(NA)
        df["_ann_raw"] = raw
        df["_ann_iso"] = raw.str.contains("-", regex=False)
    print("  %s rows=%d ISO_ann=%d (%.2f%%) NA_ann=%d"
          % (tag, len(df), int(df["_ann_iso"].sum()),
             100.0 * float(df["_ann_iso"].mean()),
             int((df["_ann_n"] == NA).sum())))
    return df


def q1_donly_keys(name, keys):
    print("\n" + "=" * 70)
    print("Q1  %s : D-only 键下钻  keys=%s" % (name, keys))
    print("=" * 70)
    d = load(os.path.join(D_DIR, name + ".parquet"), keys, "D")
    e = load(os.path.join(E_DIR, name + ".parquet"), keys, "E")

    dk = set(d["_key"].unique().tolist())
    ek = set(e["_key"].unique().tolist())
    donly = dk - ek
    print("D_only keys=%d" % len(donly))
    if not donly:
        return

    dd = d[d["_key"].isin(donly)]
    # 逐分量拆解：究竟是哪个分量让 D 行在 E 侧找不到对应
    cmp_cols = [c for c in keys]
    print("D-only 行数=%d 涉及股票=%d"
          % (len(dd), dd["ts_code"].nunique()))

    # 用 (ts_code,end_date) 做「宽松匹配」，看 E 侧是否有同期报告期的行
    if "end_date" in keys and "ann_date" in keys:
        loose = ["ts_code"] + [c for c in ("end_date",) if c in keys]
        de = set(map(tuple, d[loose].drop_duplicates().to_numpy()))
        ee = set(map(tuple, e[loose].drop_duplicates().to_numpy()))
        dd_loose = set(map(tuple, dd[loose].drop_duplicates().to_numpy()))
        both = dd_loose & ee
        print("宽松键(ts_code,end_date): D_only=%d 其中E也有同期报告期=%d "
              "E完全没有=%d" % (len(dd_loose), len(both), len(dd_loose - ee)))
        if both:
            print("  -> 结论倾向：E 有同报告期行，仅 ann_date/其余分量不同"
                  "（E 尚未覆盖该次公告），非数据缺失")

    # 逐分量值分布，看差异集中在哪一列
    for c in cmp_cols:
        if c == "ts_code":
            continue
        vals = dd[c].astype("string").fillna(NA)
        iso = vals.str.contains("-", regex=False).sum() if is_date_col(c) else 0
        top = Counter(vals.tolist()).most_common(3)
        print("  分量 %-12s 唯一值=%d  ISO形态=%d  top=%s"
              % (c, vals.nunique(), int(iso), top))

    # E 侧是否已有同 (ts_code,end_date) 且 ann_date 更晚/更早的行。
    # 判定意义：
    #   D 更晚 -> D 存的是 E 未收录的「修订版公告」，两行都该留（版本链）
    #   D 更早 -> E 用更晚的公告覆盖了同报告期，D-only 行是过期版本
    if "ann_date" in keys:
        e_by_le = {}
        for code, en, an in (e[["ts_code", "_end_n", "_ann_n"]]
                             .drop_duplicates().to_numpy()):
            e_by_le.setdefault((code, en), set()).add(an)
        later = same = 0
        pairs = Counter()
        for code, en, an in (dd[["ts_code", "_end_n", "_ann_n"]]
                             .drop_duplicates().to_numpy()):
            got = e_by_le.get((code, en))
            if not got:
                continue
            later_here = [x for x in got if x != NA and x > an]
            if later_here:
                later += 1
                pairs["D_earlier_than_E"] += 1
            elif any(x == an for x in got):
                same += 1
                pairs["ann_equal_but_key_differs"] += 1
            else:
                pairs["no_comparable"] += 1
        print("  E 侧同 (ts_code,end_date) 的 ann_date 比较: "
              "D更早(E有更新公告)=%d  ann相等(键仍不同)=%d  明细=%s"
              % (later, same, dict(pairs)))


def q2_dividend_pit():
    print("\n" + "=" * 70)
    print("Q2  dividend 合并后残余 PIT 泄漏根因")
    print("=" * 70)
    keys = TARGETS["dividend"]
    d = load(os.path.join(D_DIR, "dividend.parquet"), keys, "D")
    e = load(os.path.join(E_DIR, "dividend.parquet"), keys, "E")

    # 复现八表探针的合并口径：E 胜出、D-only 保留
    e_last = e[~e["_key"].duplicated(keep="last")]
    d_keep = d[~d["_key"].duplicated(keep="last")]
    e_keys = set(e_last["_key"].tolist())
    d_only = d_keep[~d_keep["_key"].isin(e_keys)]
    merged = pd.concat([e_last, d_only], ignore_index=True)
    print("merged rows=%d  其中来自E=%d 来自D-only=%d"
          % (len(merged), len(e_last), len(d_only)))

    iso = merged["_ann_iso"]
    print("merged ISO_ann rows=%d (%.2f%%)" % (int(iso.sum()),
                                                100.0 * float(iso.mean())))
    print("merged 中 ISO 行的来源分布: %s"
          % dict(Counter(merged.loc[iso, "_side"].tolist())))

    # 定位实际泄漏行
    asofs = ["%d%02d%02d" % (y, m, dd)
             for y in range(2018, 2027)
             for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31))]
    ann_s = merged["ann_date"].astype("string").fillna("nan").to_numpy()
    ann_n = merged["_ann_n"].to_numpy()
    leaked = np.zeros(len(merged), dtype=bool)
    for a in asofs:
        leaked |= (ann_s <= a) & ~(ann_n <= a)
    print("合并口径下累计泄漏行=%d 涉及股票=%d"
          % (int(leaked.sum()), merged.loc[leaked, "ts_code"].nunique()))
    if int(leaked.sum()):
        lk = merged[leaked]
        print("  泄漏行来源: %s" % dict(Counter(lk["_side"].tolist())))
        print("  泄漏行 ISO 形态: %d/%d"
              % (int(lk["_ann_iso"].sum()), len(lk)))
        print("  泄漏行 ann_date 原始值 top5: %s"
              % Counter(lk["_ann_raw"].tolist()).most_common(5))
        print("  泄漏行 ann_date 归一值 top5: %s"
              % Counter(lk["_ann_n"].tolist()).most_common(5))
        print("  泄漏是否全部集中在 ISO 行: %s"
              % bool((lk["_ann_iso"]).all()))
        # 若泄漏行清一色 ISO，则根因就是 astock_finance_reader 的字符串比较，
        # 而非合并规则本身
        non_iso_leak = int((~lk["_ann_iso"]).sum())
        print("  非 ISO 行也泄漏的数量=%d （>0 说明合并规则另有问题）"
              % non_iso_leak)

    # no_true=11：这些股票在 as-of 点下连一条真实可见行都没有
    print("\n -- no_true 诊断 --")
    dd2 = merged.copy()
    dd2["_ann_s"] = dd2["ann_date"].astype("string").fillna("nan")
    dd2 = dd2.sort_values(["ts_code", "_ann_s"], ascending=[True, False],
                          kind="stable")
    no_true_codes = set()
    no_true_pts = Counter()
    for a in asofs:
        tv = dd2[dd2["_ann_n"] <= a]
        tv_ok = tv["_end_n"] != NA
        best_map = tv.loc[tv_ok].groupby("ts_code", sort=False)["_end_n"].max()
        vis = dd2[dd2["_ann_s"] <= a]
        if len(vis) == 0:
            continue
        picked = vis.drop_duplicates("ts_code")
        bestv = picked["ts_code"].map(best_map).astype("string").fillna(NA)
        hit = picked.loc[bestv == NA, "ts_code"].tolist()
        if hit:
            no_true_codes.update(hit)
            no_true_pts[a] = len(hit)
    print("no_true 股票数=%d -> %s"
          % (len(no_true_codes), sorted(no_true_codes)))
    print("  触发 as-of 点 top5: %s" % no_true_pts.most_common(5))
    if no_true_codes:
        sub = merged[merged["ts_code"].isin(no_true_codes)]
        print("  这些股票在 merged 中的行数=%d ann_date 范围=%s ~ %s"
              % (len(sub), sub["_ann_n"].min(), sub["_ann_n"].max()))
        clean = sub["_ann_n"].replace(NA, np.nan).dropna()
        print("  最早可见 ann_date=%s"
              % (clean.min() if len(clean) else NA))

    # Q2b 判定实验：把 ann_date/end_date 归一化成 YYYYMMDD 字符串后重算泄漏。
    # 泄漏归零 -> 根因确认是「日期格式混用」，修写入侧归一化即可，
    #              无需改 astock_finance_reader 的比较逻辑。
    # 泄漏不归零 -> 根因在数据本身（公告日本身错），必须改读取器或剔除。
    print("\n -- Q2b 判定实验：归一化日期格式后重算泄漏 --")
    fixed = merged.copy()
    fixed["ann_date"] = fixed["_ann_n"]
    fixed["end_date"] = fixed["_end_n"]
    ann_s2 = fixed["ann_date"].astype("string").fillna("nan").to_numpy()
    ann_n2 = fixed["_ann_n"].to_numpy()
    leak_before = int(leaked.sum())
    leak_after = np.zeros(len(fixed), dtype=bool)
    for a in asofs:
        leak_after |= (ann_s2 <= a) & ~(ann_n2 <= a)
    print("归一化前 distinct 泄漏行=%d  归一化后=%d  -> %s"
          % (leak_before, int(leak_after.sum()),
             "根因=日期格式混用" if int(leak_after.sum()) == 0
             else "根因=数据本身，仍需排查"))


def main():
    print("FINANCE RESIDUAL DRILLDOWN (read-only)")
    fails = []
    for name in ("fina_indicator", "forecast"):
        try:
            q1_donly_keys(name, TARGETS[name])
        except Exception as exc:
            print("  !! Q1 %s FAILED %r" % (name, exc))
            import traceback
            traceback.print_exc()
            fails.append("Q1:" + name)
    try:
        q2_dividend_pit()
    except Exception as exc:
        print("  !! Q2 FAILED %r" % (exc))
        import traceback
        traceback.print_exc()
        fails.append("Q2:dividend")

    print("\nDRILLDOWN_DONE failed=%s" % (",".join(fails) if fails else "none"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())