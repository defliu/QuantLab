# coding: utf-8
"""E 独有行（拟追加区间）质量体检（只读）。

决策依赖：日线若采取「只追加 E 独有行」，那么真正要写进 D 盘的是
2026-09-14 ~ 2026-09-24 这 9 个交易日。此前的换手率×100 单位错误是在
2026-08-20 的**共有**行上验证的，不能假定新行同样干净——必须单独体检：

  1. 换手率量纲：E 独有行的 turnover_rate 是否也已 /100
  2. 复权因子：adj_factor 是否有 NaN（共有行已发现 4 条）
  3. 关键列缺失率：与 D 同期行对照
  4. 唯一性/索引：E 独有键是否本身无重复
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

d = pd.read_parquet(D_PATH, columns=["turnover_rate",
                                    "turnover_rate_f", "adj_factor", "close",
                                    "vol", "total_share", "float_share"])
e = pd.read_parquet(E_PATH)
print("D rows=%d  E rows=%d" % (len(d), len(e)))
print("D index=%s names=%s | E index=%s"
      % (type(d.index).__name__, list(d.index.names), type(e.index).__name__))

# D 侧 trade_date/ts_code 藏在 MultiIndex 里，必须 reset_index 才能按列比较。
# 这本身就是「E 的 RangeIndex 不能原样覆盖 D」的又一佐证。
d = d.reset_index()
e = pd.read_parquet(E_PATH)

d["trade_date"] = d["trade_date"].astype(str).str.replace("-", "", regex=False)
e["trade_date"] = e["trade_date"].astype(str).str.replace("-", "", regex=False)
e["ts_code"] = e["ts_code"].astype(str)

dk = set(map(tuple, d[["trade_date", "ts_code"]].drop_duplicates().to_numpy()))
ek = set(map(tuple, e[["trade_date", "ts_code"]].drop_duplicates().to_numpy()))
eonly = ek - dk
print("E_only 键=%d  日期范围=%s ~ %s"
      % (len(eonly), min(k[0] for k in eonly), max(k[0] for k in eonly)))

# 向量化筛 E 独有行：先建 key 列再用 isin，避免在 1467 万行上 apply(axis=1)
e["_k"] = e["trade_date"] + "\u0001" + e["ts_code"]
d["_k"] = d["trade_date"] + "\u0001" + d["ts_code"]
eonly_k = set("%s\u0001%s" % (a, b) for a, b in eonly)
eo = e[e["_k"].isin(eonly_k)].copy()
print("E 独有行=%d  涉及股票=%d  涉及交易日=%d"
      % (len(eo), eo["ts_code"].nunique(), eo["trade_date"].nunique()))
print("按交易日分布:")
print(eo.groupby("trade_date").size().to_string())

print("\n-- 1. 换手率量纲体检（E 独有行）--")
for col in ("turnover_rate", "turnover_rate_f"):
    if col not in eo.columns:
        continue
    v = pd.to_numeric(eo[col], errors="coerce")
    # 用 vol/float_share 独立算出真值做交叉校验
    if col == "turnover_rate" and "total_share" in eo.columns:
        base = "total_share"
    elif col == "turnover_rate_f" and "float_share" in eo.columns:
        base = "float_share"
    else:
        base = None
    if base:
        truth = pd.to_numeric(eo["vol"], errors="coerce") / pd.to_numeric(
            eo[base], errors="coerce")
        ok = v.notna() & truth.notna() & (truth > 0)
        r1 = float(np.nanmedian((v[ok] / truth[ok]).to_numpy()))
        r100 = float(np.nanmedian((v[ok] / (truth[ok] * 100)).to_numpy()))
        print("  %-16s median(vol/expect)=%.4f  median(vol/expect/100)=%.4f"
              % (col, r1, r100))
        print("      -> E 独有行口径: %s"
              % ("与 D 同口径(未/100)" if abs(r1 - 1.0) < 0.02
                 else "已/100（错误口径！）" if abs(r1 - 0.01) < 0.002
                 else "无法判定"))
        print("      描述: p1/p50/p99=%.4f/%.4f/%.4f"
              % tuple(v.quantile([0.01, 0.5, 0.99]).tolist()))

print("\n-- 2. adj_factor 缺失（E 独有行）--")
na_af = int(eo["adj_factor"].isna().sum())
print("  adj_factor NaN=%d / %d" % (na_af, len(eo)))
if na_af:
    print(eo.loc[eo["adj_factor"].isna(), ["trade_date", "ts_code"]]
          .to_string(index=False))

print("\n-- 3. 关键列缺失率对照 --")
cols = ["close", "vol", "total_share", "float_share", "turnover_rate",
        "turnover_rate_f", "adj_factor"]
dmax = d["trade_date"].max()
dref = d[d["trade_date"] >= "20260820"]
for c in cols:
    if c not in eo.columns or c not in dref.columns:
        continue
    print("  %-16s E独有缺失=%6.2f%%   D(0820起)缺失=%6.2f%%"
          % (c, 100.0 * float(eo[c].isna().mean()),
             100.0 * float(dref[c].isna().mean())))

print("\n-- 4. 唯一性 --")
print("  E 独有键重复数=%d"
      % (len(eo) - eo[["trade_date", "ts_code"]]
         .drop_duplicates().shape[0]))

print("\n-- 5. E 独有行 vs D 最后交易日 字段口径对照 --")
lastd = d[d["trade_date"] == dmax]
print("  D 最后交易日=%s rows=%d" % (dmax, len(lastd)))
for c in ["close", "adj_factor"]:
    if c in lastd.columns and c in eo.columns:
        a = pd.to_numeric(lastd[c], errors="coerce")
        b = pd.to_numeric(eo[c], errors="coerce")
        print("    %-12s D末日中位=%.4f  E独有中位=%.4f"
              % (c, float(a.median()), float(b.median())))
print("NEWROWS_DONE")