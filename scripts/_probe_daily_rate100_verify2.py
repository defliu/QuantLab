# coding: utf-8
"""核实 3 个缺陷日的全列 D/E 差异 + 精度假设（只读）。

locate 脚本发现缺陷只在 20260820/0825/0827；
verify 脚本发现 D 在 0825/0827 的 5 列全 NaN（11096 行空洞），
但 Q4「D 与 E×100 逐行相等只有 7/5541」与 Q3「E×100 命中 100%」表面矛盾。

本脚本判定矛盾根因，并给出可执行的合并结论：
  A1 D/E turnover_rate 落盘小数位精度对比（验证 Q4 是精度假象）
  A2 D(20260820) vs E×100 在多档容差下的相等率
  A3 float_share 302 行不一致的性质（小漂移 or 大差异）
  A4 0825/0827 上 E 能否补上 D 的 5 列空洞（修复后是否自洽）
  A5 3 个缺陷日的全列 D/E 差异清单（合并到底会改什么）
  A6 另一个 E 文件（增量版）是否也有同样缺陷（判断是供应商批次问题）

只统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"
E_INCR = r"E:\temp\行情数据\stock_daily.parquet"

BAD_DATES = ["20260820", "20260825", "20260827"]
RATE_DEN = {"turnover_rate": "float_share",
            "turnover_rate_f": "free_share"}


def norm_td(s):
    return s.astype(str).str.replace("-", "", regex=False)


def decimals(series, cap=8):
    def one(v):
        if not np.isfinite(v):
            return -1
        s = repr(float(v))
        if "e" in s or "E" in s:
            return cap
        if "." not in s:
            return 0
        return min(cap, len(s.split(".")[1].rstrip("0")))
    return series.map(one).to_numpy("int64")


def main():
    e = pd.read_parquet(E_PATH)
    e["_td"] = norm_td(e["trade_date"])
    e["_k"] = e["_td"] + "\u0001" + e["ts_code"].astype(str)

    d = pd.read_parquet(D_PATH).reset_index()
    d["_td"] = norm_td(d["trade_date"])
    d["_k"] = d["_td"] + "\u0001" + d["ts_code"].astype(str)

    # ---------- A1 精度 ----------
    print("=" * 78)
    print("A1  turnover_rate 落盘小数位数分布")
    print("=" * 78)
    for tag, df in (("D", d), ("E", e)):
        for rc in RATE_DEN:
            v = pd.to_numeric(df[rc], errors="coerce")
            dec = decimals(v)
            u, c = np.unique(dec[dec >= 0], return_counts=True)
            top = sorted(zip(c, u), reverse=True)[:4]
            print("  %s %-16s 小数位 top: %s"
                  % (tag, rc, ", ".join("%d位=%d" % (uu, cc)
                                       for cc, uu in top)))

    # ---------- A2 多档容差 ----------
    print("\n" + "=" * 78)
    print("A2  D(20260820).turnover_rate  vs  E(20260820).turnover_rate×100")
    print("=" * 78)
    dd20 = d[d["_td"] == "20260820"]
    ee20 = e[e["_td"] == "20260820"]
    m20 = ee20.merge(dd20, on="_k", suffixes=("_e", "_d"), how="inner")
    for rc in RATE_DEN:
        ve = pd.to_numeric(m20["%s_e" % rc], errors="coerce").to_numpy("float64")
        vd = pd.to_numeric(m20["%s_d" % rc], errors="coerce").to_numpy("float64")
        both = np.isfinite(ve) & np.isfinite(vd)
        fixed = ve[both] * 100.0
        ref = vd[both]
        print("  %s  双方非空=%d" % (rc, int(both.sum())))
        for name, rtol, atol in (("严格 rtol1e-9", 1e-9, 1e-12),
                                 ("rtol1e-4", 1e-4, 1e-12),
                                 ("rtol1e-3", 1e-3, 1e-9),
                                 ("atol1e-4", 0.0, 1e-4)):
            hit = np.isclose(fixed, ref, rtol=rtol, atol=atol)
            print("    %-16s 命中=%-6d (%.2f%%)"
                  % (name, int(hit.sum()),
                     100.0 * hit.sum() / max(1, both.sum())))
        diff = np.abs(fixed - ref)
        print("    绝对差 p50=%.3e p99=%.3e max=%.3e"
              % (np.nanpercentile(diff, 50), np.nanpercentile(diff, 99),
                 np.nanmax(diff)))

    # ---------- A3 float_share 差异性质 ----------
    print("\n" + "=" * 78)
    print("A3  20260820 float_share / free_share 差异性质")
    print("=" * 78)
    for c in ("total_share", "float_share", "free_share"):
        ve = pd.to_numeric(m20["%s_e" % c], errors="coerce").to_numpy("float64")
        vd = pd.to_numeric(m20["%s_d" % c], errors="coerce").to_numpy("float64")
        both = np.isfinite(ve) & np.isfinite(vd)
        rel = np.abs(ve[both] - vd[both]) / np.maximum(
            np.abs(vd[both]), 1e-12)
        nz = rel[rel > 1e-9]
        print("  %-13s 双方非空=%-6d 有差异=%-6d 相对差 p50=%.3e max=%.3e"
              % (c, int(both.sum()), int(nz.size),
                 np.nanpercentile(nz, 50) if nz.size else 0.0,
                 np.nanmax(nz) if nz.size else 0.0))
        if nz.size:
            big = int((nz > 1e-3).sum())
            print("               其中相对差>0.1%% 的=%d (%.2f%%)"
                  % (big, 100.0 * big / nz.size))

    # ---------- A4 0825/0827 空洞可补性 ----------
    print("\n" + "=" * 78)
    print("A4  0825/0827：E 能否补上 D 的 5 列空洞")
    print("=" * 78)
    fill_cols = ["total_share", "float_share", "free_share",
                 "turnover_rate", "turnover_rate_f"]
    for dt in ["20260825", "20260827"]:
        ds = d[d["_td"] == dt]
        es = e[e["_td"] == dt]
        mm = es.merge(ds, on="_k", suffixes=("_e", "_d"), how="inner")
        print("  %s merge=%d" % (dt, len(mm)))
        for c in fill_cols:
            ve = pd.to_numeric(mm["%s_e" % c], errors="coerce").to_numpy("float64")
            vd = pd.to_numeric(mm["%s_d" % c], errors="coerce").to_numpy("float64")
            print("    %-16s D非空=%-6d E非空=%-6d 互补(D空E有)=%-6d"
                  % (c, int(np.isfinite(vd).sum()), int(np.isfinite(ve).sum()),
                     int((~np.isfinite(vd) & np.isfinite(ve)).sum())))
        for rc, dc in RATE_DEN.items():
            vol = pd.to_numeric(mm["vol_e"], errors="coerce").to_numpy("float64")
            rate = pd.to_numeric(mm["%s_e" % rc], errors="coerce").to_numpy("float64")
            den = pd.to_numeric(mm["%s_e" % dc], errors="coerce").to_numpy("float64")
            ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
                  & np.isfinite(den) & (den > 0) & (rate > 0))
            recalc = vol[ok] / den[ok]
            hit = np.isclose(rate[ok] * 100.0 / recalc - 1.0, 0.0,
                             rtol=1e-3, atol=0.0)
            print("    修复校验 %-16s ×100后自洽=%d/%d (%.2f%%)"
                  % (rc, int(hit.sum()), int(ok.sum()),
                     100.0 * hit.sum() / max(1, ok.sum())))

    # ---------- A5 全列差异 ----------
    print("\n" + "=" * 78)
    print("A5  3 个缺陷日 全列 D vs E 差异")
    print("=" * 78)
    ds = d[d["_td"].isin(BAD_DATES)]
    es = e[e["_td"].isin(BAD_DATES)]
    m = es.merge(ds, on="_k", suffixes=("_e", "_d"), how="outer")
    print("merge=%d" % len(m))
    common = [c for c in d.columns
              if c in e.columns and c not in ("_td", "_k", "trade_date",
                                              "ts_code")]
    print("  %-18s %10s %10s %10s" % ("列", "双方非空", "完全相同", "一致率%"))
    for c in common:
        a, b = m["%s_e" % c], m["%s_d" % c]
        na = pd.to_numeric(a, errors="coerce").to_numpy("float64")
        nb = pd.to_numeric(b, errors="coerce").to_numpy("float64")
        if np.isfinite(na).any() or np.isfinite(nb).any():
            both = np.isfinite(na) & np.isfinite(nb)
            eq = int(np.isclose(na[both], nb[both], rtol=1e-6,
                                atol=1e-9, equal_nan=True).sum())
            tot = int(both.sum())
            na_txt = int((~np.isfinite(na) & np.isfinite(nb)).sum())
            nb_txt = int((np.isfinite(na) & ~np.isfinite(nb)).sum())
            flag = "  <<<" if (tot and eq < tot) else ""
            print("  %-18s %10d %10d %9.2f%%  D空E有=%d E空D有=%d%s"
                  % (c, tot, eq, 100.0 * eq / max(1, tot),
                     na_txt, nb_txt, flag))
        else:
            both = (~a.isna()) & (~b.isna())
            eq = int((a[both].astype(str) == b[both].astype(str)).sum())
            tot = int(both.sum())
            flag = "  <<<" if (tot and eq < tot) else ""
            print("  %-18s %10d %10d %9.2f%% (字符串)%s"
                  % (c, tot, eq, 100.0 * eq / max(1, tot), flag))

    # ---------- A6 增量文件 ----------
    print("\n" + "=" * 78)
    print("A6  E 增量文件是否同源缺陷")
    print("=" * 78)
    try:
        inc = pd.read_parquet(E_INCR)
        inc["_td"] = norm_td(inc["trade_date"])
        print("  增量文件行=%d 日期=%s ~ %s"
              % (len(inc), inc["_td"].min(), inc["_td"].max()))
        for dt in BAD_DATES:
            s = inc[inc["_td"] == dt]
            print("    含 %s 行=%d" % (dt, len(s)))
        for rc, dc in RATE_DEN.items():
            vol = pd.to_numeric(inc["vol"], errors="coerce").to_numpy("float64")
            rate = pd.to_numeric(inc[rc], errors="coerce").to_numpy("float64")
            den = pd.to_numeric(inc[dc], errors="coerce").to_numpy("float64")
            ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
                  & np.isfinite(den) & (den > 0) & (rate > 0))
            r = np.full(len(inc), np.nan)
            r[ok] = rate[ok] / (vol[ok] / den[ok])
            print("    %-16s ratio≈0.01=%-6d ratio≈1=%-6d 可用=%d"
                  % (rc, int((ok & (np.abs(r - 0.01) <= 1e-5)).sum()),
                     int((ok & (np.abs(r - 1.0) <= 1e-3)).sum()), int(ok.sum())))
    except Exception as exc:
        print("  读取失败: %r" % (exc,))

    print("\nLOCATE_VERIFY2_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())