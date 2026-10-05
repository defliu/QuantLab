# coding: utf-8
"""定点确认两个剩余歧义（只读）。

A) forecast 414 组 D-only：ann_date 与 E 相同，说明差异不在公告日，
   只可能在 type（第 4 个主键分量）。需要确认 D/E 的 type 取值分布，
   判断这是「E 改了分类」还是「同一公告被写成不同 type」。

B) dividend E 侧 97 条 ISO 日期行：是否恰好等于 E_only 键集合。
   若成立，说明 E 新增的分红记录整批用 datetime64 而非 YYYYMMDD 字符串，
   属于 schema 异构，朴素 concat 会把混合格式带进 D 盘。
"""
import os
import sys
from collections import Counter

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _keynorm import NA, SEP, is_date_col, norm_date, norm_scalar  # noqa: E402

D_DIR = r"D:/astock/finance"
E_DIR = r"E:\temp\行情数据"


def keyed(path, keys, tag):
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
    df["_end_n"] = df["end_date"].map(norm_date)
    df["_ann_n"] = df["ann_date"].map(norm_date)
    raw = df["ann_date"].astype("string").fillna(NA)
    df["_ann_iso"] = raw.str.contains("-", regex=False)
    # 原始 dtype 判定：区分「字符串列」与「真 datetime 列」
    df["_ann_dtype"] = str(df["ann_date"].dtype)
    return df


print("=" * 70)
print("A) forecast D-only 414 组的 type 分量差异")
print("=" * 70)
keys = ["ts_code", "end_date", "ann_date", "type"]
d = keyed(os.path.join(D_DIR, "forecast.parquet"), keys, "D")
e = keyed(os.path.join(E_DIR, "forecast.parquet"), keys, "E")
print("D type 分布: %s" % Counter(d["type"].astype(str).tolist()).most_common())
print("E type 分布: %s" % Counter(e["type"].astype(str).tolist()).most_common())
print("D ann_date dtype=%s | E ann_date dtype=%s"
      % (d["_ann_dtype"].iloc[0], e["_ann_dtype"].iloc[0]))

dk = set(d["_key"].unique().tolist())
ek = set(e["_key"].unique().tolist())
dd = d[d["_key"].isin(dk - ek)]

# 对每个 D-only 行，按 (ts_code,end_date,ann_date) 三元组去 E 侧找对应行
loose_keys = ["ts_code", "_end_n", "_ann_n"]
idx = {}
for code, en, an, ty in e[loose_keys + ["type"]].drop_duplicates().to_numpy():
    idx.setdefault((code, en, an), set()).add(ty)

pair = Counter()
samples = []
for code, en, an, ty in (dd[loose_keys + ["type"]]
                         .drop_duplicates().to_numpy()):
    got = idx.get((code, en, an))
    if not got:
        pair["E无对应行"] += 1
        continue
    got_s = {str(x) for x in got}
    if str(ty) in got_s:
        pair["type相同(键不该不同!)"] += 1
    else:
        pair["type不同"] += 1
        if len(samples) < 8:
            samples.append((code, en, an, str(ty), sorted(got_s)))
print("三键(ts_code,end_date,ann_date)匹配下的 type 关系: %s"
      % dict(pair))
for s in samples:
    print("   %s end=%s ann=%s  D.type=%r  E.type=%s" % s)

# E 侧同三键的 type 是否为 D 的超集/子集
d_tri = set(map(tuple, d[loose_keys].drop_duplicates().to_numpy()))
e_tri = set(map(tuple, e[loose_keys].drop_duplicates().to_numpy()))
print("三键集合: D=%d E=%d D_only=%d E_only=%d"
      % (len(d_tri), len(e_tri), len(d_tri - e_tri), len(e_tri - d_tri)))

print("\n" + "=" * 70)
print("B) dividend E 侧 ISO 日期行 是否 == E_only 键集合")
print("=" * 70)
keys = ["ts_code", "end_date", "ann_date", "div_proc"]
d2 = keyed(os.path.join(D_DIR, "dividend.parquet"), keys, "D")
e2 = keyed(os.path.join(E_DIR, "dividend.parquet"), keys, "E")
print("D ann_date dtype=%s ISO=%d | E ann_date dtype=%s ISO=%d"
      % (d2["_ann_dtype"].iloc[0], int(d2["_ann_iso"].sum()),
         e2["_ann_dtype"].iloc[0], int(e2["_ann_iso"].sum())))

dk2 = set(d2["_key"].unique().tolist())
ek2 = set(e2["_key"].unique().tolist())
eonly = ek2 - dk2
e_iso_keys = set(e2.loc[e2["_ann_iso"], "_key"].unique().tolist())
print("E_only 键=%d  E 中 ISO 行的键=%d  两者交集=%d"
      % (len(eonly), len(e_iso_keys), len(eonly & e_iso_keys)))
print("ISO 键是否完全等于 E_only: %s" % bool(e_iso_keys == eonly))
print("E 侧 ISO 行的 ts_code 样例: %s"
      % sorted(e2.loc[e2["_ann_iso"], "ts_code"].unique().tolist())[:6])

# end_date 是否也混用格式（决定归一化要覆盖几列）
for nm, df in (("D", d2), ("E", e2)):
    for col in ("end_date", "ann_date"):
        if col in df.columns:
            raw = df[col].astype("string").fillna(NA)
            iso = int(raw.str.contains("-", regex=False).sum())
            print("  %s %s dtype=%s ISO行=%d/%d"
                  % (nm, col, df[col].dtype, iso, len(df)))
print("DRILL2_DONE")