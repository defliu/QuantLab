# coding: utf-8
"""核实换手率 /100 缺陷的三个关键问题（只读）。

由 _probe_daily_rate100_locate.py 发现：
  缺陷仅集中在 3 个交易日 20260820 / 20260825 / 20260827
  D 侧 20260825、20260827 的 float_share / turnover_rate 似乎为 NaN

本脚本核实：
  Q1 D 侧这 3 天到底有没有行、哪些列是 NaN（D 是否本来就有缺口）
  Q2 E×100 能否在全部缺陷行上重建 vol/float_share（自校验修复规则）
  Q3 D 侧 20260820 的值是否恰好等于 E×100（证明修复倍数正确）
  Q4 缺陷是否波及其他列（close/amount/total_share/free_share 等）
  Q5 相邻交易日 20260821/0824/0826 在 E 侧是否干净（界定影响边界）

只统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

BAD_DATES = ["20260820", "20260825", "20260827"]
NEIGHBOR_DATES = ["20260819", "20260821", "20260824", "20260826", "20260828"]
RATE_DEN = {"turnover_rate": "float_share",
            "turnover_rate_f": "free_share"}
TOL = 1e-3


def norm_td(s):
    return s.astype(str).str.replace("-", "", regex=False)


def key_of(df, td, code):
    return norm_td(td) + "\u0001" + code.astype(str)


def main():
    e = pd.read_parquet(E_PATH)
    e["_td"] = norm_td(e["trade_date"])
    e["_k"] = e["_td"] + "\u0001" + e["ts_code"].astype(str)
    print("E 全表列: %s" % list(e.columns))

    d = pd.read_parquet(D_PATH)
    d = d.reset_index()
    d["_td"] = norm_td(d["trade_date"])
    d["_k"] = d["_td"] + "\u0001" + d["ts_code"].astype(str)
    print("D 全表列: %s" % list(d.columns))

    # ---------- Q1 D 侧这 3 天有没有行 / 哪些列 NaN ----------
    print("\n" + "=" * 78)
    print("Q1  D 侧关键日期的行数与列缺失")
    print("=" * 78)
    probe_cols = ["vol", "close", "amount", "total_share", "float_share",
                  "free_share", "turnover_rate", "turnover_rate_f",
                  "adj_factor"]
    have = [c for c in probe_cols if c in d.columns]
    for dt in BAD_DATES + NEIGHBOR_DATES:
        sub = d[d["_td"] == dt]
        row = "  %s  D行=%-6d" % (dt, len(sub))
        if len(sub):
            na = {c: int(sub[c].isna().sum()) for c in have}
            bad = {c: v for c, v in na.items() if v}
            row += "  全NaN列=%s" % (bad if bad else "无")
        print(row)
        esub = e[e["_td"] == dt]
        row = "  %s  E行=%-6d" % ("", len(esub))
        if len(esub):
            na = {c: int(esub[c].isna().sum())
                  for c in probe_cols if c in esub.columns}
            bad = {c: v for c, v in na.items() if v}
            row += "  全NaN列=%s" % (bad if bad else "无")
        print(row)

    # ---------- Q2/Q3/Q4 逐列对比这 3 天 ----------
    print("\n" + "=" * 78)
    print("Q2-Q4  3 个缺陷日 D vs E 全列对比")
    print("=" * 78)
    dsub = d[d["_td"].isin(BAD_DATES)]
    esub = e[e["_td"].isin(BAD_DATES)]
    m = esub.merge(dsub, on="_k", suffixes=("_e", "_d"), how="outer",
                   indicator=True)
    print("merge 行=%d  (_merge: %s)"
          % (len(m), m["_merge"].value_counts().to_dict()))

    cmp_cols = [c for c in probe_cols if ("%s_e" % c) in m.columns
                and ("%s_d" % c) in m.columns]
    print("\n列级一致率（仅双方均非空的行）:")
    for c in cmp_cols:
        ve = pd.to_numeric(m["%s_e" % c], errors="coerce")
        vd = pd.to_numeric(m["%s_d" % c], errors="coerce")
        both = ve.notna() & vd.notna()
        eq = np.isclose(ve[both], vd[both], rtol=1e-9, atol=1e-12)
        print("  %-18s 双方非空=%-7d 完全相同=%-7d (%.2f%%)  E空D非空=%-6d E非空D空=%d"
              % (c, int(both.sum()), int(eq.sum()),
                 100.0 * eq.sum() / max(1, both.sum()),
                 int((ve.isna() & vd.notna()).sum()),
                 int((ve.notna() & vd.isna()).sum())))

    # ---------- Q3 E×100 重建校验 ----------
    print("\n" + "=" * 78)
    print("Q3  E×100 能否重建 vol/分母（修复规则自校验）")
    print("=" * 78)
    for dt in BAD_DATES:
        s = e[e["_td"] == dt]
        for rc, dc in RATE_DEN.items():
            vol = pd.to_numeric(s["vol"], errors="coerce").to_numpy("float64")
            rate = pd.to_numeric(s[rc], errors="coerce").to_numpy("float64")
            den = pd.to_numeric(s[dc], errors="coerce").to_numpy("float64")
            ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
                  & np.isfinite(den) & (den > 0) & (rate > 0))
            recalc = vol[ok] / den[ok]
            fixed = rate[ok] * 100.0
            hit = np.abs(fixed / recalc - 1.0) <= TOL
            print("  %s %-16s 可用=%-6d ×100后命中recalc@%g = %d (%.2f%%)"
                  % (dt, rc, int(ok.sum()), TOL, int(hit.sum()),
                     100.0 * hit.sum() / max(1, ok.sum())))

    # ---------- Q4 D 侧 20260820 是否 = E×100 ----------
    print("\n" + "=" * 78)
    print("Q4  D(20260820) 与 E×100 是否逐行相等")
    print("=" * 78)
    dd20 = d[d["_td"] == "20260820"]
    ee20 = e[e["_td"] == "20260820"]
    m20 = ee20.merge(dd20, on="_k", suffixes=("_e", "_d"), how="inner")
    print("merge=%d" % len(m20))
    for rc in RATE_DEN:
        ve = pd.to_numeric(m20["%s_e" % rc], errors="coerce").to_numpy("float64")
        vd = pd.to_numeric(m20["%s_d" % rc], errors="coerce").to_numpy("float64")
        both = np.isfinite(ve) & np.isfinite(vd)
        eq100 = np.isclose(ve[both] * 100.0, vd[both], rtol=1e-9, atol=1e-12)
        eqraw = np.isclose(ve[both], vd[both], rtol=1e-9, atol=1e-12)
        print("  %-16s 双方非空=%-6d 原值相同=%-6d ×100后相同=%-6d"
              % (rc, int(both.sum()), int(eqraw.sum()), int(eq100.sum())))
    for c in ["float_share", "free_share", "vol", "close"]:
        if ("%s_e" % c) in m20.columns:
            ve = pd.to_numeric(m20["%s_e" % c], errors="coerce").to_numpy("float64")
            vd = pd.to_numeric(m20["%s_d" % c], errors="coerce").to_numpy("float64")
            both = np.isfinite(ve) & np.isfinite(vd)
            eq = np.isclose(ve[both], vd[both], rtol=1e-9, atol=1e-12)
            print("  %-16s 双方非空=%-6d 相同=%-6d (%.2f%%)"
                  % (c, int(both.sum()), int(eq.sum()),
                     100.0 * eq.sum() / max(1, both.sum())))

    # ---------- Q5 邻近日 E 侧是否干净 ----------
    print("\n" + "=" * 78)
    print("Q5  邻近日 E 侧倍率是否干净（界定影响边界）")
    print("=" * 78)
    for dt in ["20260819"] + BAD_DATES + ["20260821", "20260824",
                                          "20260826", "20260828"]:
        s = e[e["_td"] == dt]
        out = "  %s 行=%-6d" % (dt, len(s))
        for rc, dc in RATE_DEN.items():
            vol = pd.to_numeric(s["vol"], errors="coerce").to_numpy("float64")
            rate = pd.to_numeric(s[rc], errors="coerce").to_numpy("float64")
            den = pd.to_numeric(s[dc], errors="coerce").to_numpy("float64")
            ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
                  & np.isfinite(den) & (den > 0) & (rate > 0))
            r = np.full(len(s), np.nan)
            r[ok] = rate[ok] / (vol[ok] / den[ok])
            low = int((ok & (np.abs(r - 0.01) <= TOL * 0.01)).sum())
            out += "  %s: 低档=%d" % (rc.replace("turnover_rate", "tr"), low)
        print(out)

    print("\nLOCATE_VERIFY_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())