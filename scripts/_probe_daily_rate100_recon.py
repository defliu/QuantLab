# coding: utf-8
"""对账：E 全表 ratio≈100 的 16634 行到底在哪个键空间（只读）。

矛盾事实：
  · _probe_daily_rate100.py 报 E 全表 turnover_rate 有 16634 行 ratio≈100，
    且明确 E-only 新行 100% 正常、ratio≈100 为 0 行。
  · _probe_daily_rate100_scope.py 报共有键里 ratio≈100 为 0 行。
两者相加 = 16634 行应当既不在 E-only 也不在共有键里，但 E 只有这两个分区，
数学上不可能。

结论必然是「两脚本的键空间划分不一致」，即其中一份把 D 的键空间算错了。
本脚本直接把三个分区（E∩D / E-only / E-全体）的行数与异常行数一次性对齐，
并检查 E 的键是否存在重复（重复键会让 set 判定与 isin 判定分叉）。

只统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

RC = "turnover_rate"
DEN = "float_share"
TOL = 1e-3


def norm_td(s):
    return s.astype(str).str.replace("-", "", regex=False)


def ratio_of(df):
    vol = pd.to_numeric(df["vol"], errors="coerce").to_numpy(dtype="float64")
    rate = pd.to_numeric(df[RC], errors="coerce").to_numpy(dtype="float64")
    den = pd.to_numeric(df[DEN], errors="coerce").to_numpy(dtype="float64")
    ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
          & np.isfinite(den) & (den > 0) & (rate > 0))
    out = np.full(len(df), np.nan, dtype="float64")
    out[ok] = rate[ok] / (vol[ok] / den[ok])
    return out


def main():
    e = pd.read_parquet(
        E_PATH,
        columns=["trade_date", "ts_code", "vol", DEN, RC],
    )
    e["trade_date"] = norm_td(e["trade_date"])
    e["ts_code"] = e["ts_code"].astype(str)
    e["_k"] = e["trade_date"] + "\u0001" + e["ts_code"]

    dk = pd.read_parquet(D_PATH, columns=["trade_date", "ts_code"])
    if isinstance(dk.index, pd.MultiIndex):
        dk = dk.reset_index()
    dk["trade_date"] = norm_td(dk["trade_date"])
    dk["ts_code"] = dk["ts_code"].astype(str)
    dk["_k"] = dk["trade_date"] + "\u0001" + dk["ts_code"]

    print("=" * 78)
    print("A. 行数与键唯一性")
    print("=" * 78)
    print("E rows=%d  E unique keys=%d  E dup keys=%d"
          % (len(e), e["_k"].nunique(), len(e) - e["_k"].nunique()))
    print("D rows=%d  D unique keys=%d  D dup keys=%d"
          % (len(dk), dk["_k"].nunique(), len(dk) - dk["_k"].nunique()))

    dset = set(dk["_k"].tolist())
    in_d = e["_k"].isin(dset)
    eo_mask = ~in_d
    print("\n分区: E∩D=%d  E-only=%d  合计=%d"
          % (int(in_d.sum()), int(eo_mask.sum()), len(e)))

    print("\n" + "=" * 78)
    print("B. 异常行落在哪个分区")
    print("=" * 78)
    r = ratio_of(e)
    ok = np.isfinite(r)
    bad = ok & (np.abs(r - 100.0) <= TOL * 100)
    print("E 全表 可用行=%d  ratio≈100=%d"
          % (int(ok.sum()), int(bad.sum())))

    m_bad = ok & in_d.to_numpy()
    m_eo = ok & eo_mask.to_numpy()
    print("  其中 E∩D 分区 : %d" % int((bad & m_bad).sum()))
    print("  其中 E-only 分区: %d" % int((bad & m_eo).sum()))
    print("  两者之和       : %d  (应等于全表 %d)"
          % (int((bad & m_bad).sum()) + int((bad & m_eo).sum()),
             int(bad.sum())))

    print("\n" + "=" * 78)
    print("C. 若异常全在 E∩D：核对 D 侧同键表现")
    print("=" * 78)
    if int((bad & m_bad).sum()) == 0:
        print("E∩D 分区内无异常行 —— 与 scope 脚本一致")
        print("则异常行只可能在 E-only；但上一步实测 E-only 100% 正常，")
        print("说明两次运行的 E 读取结果不同，需检查文件是否被改动。")
    else:
        badk = set(e.loc[bad & m_bad, "_k"].tolist())
        dd = dk[dk["_k"].isin(badk)].copy()
        # 把 D 同键的分母/成交额取来重算
        ebd = e.loc[bad & m_bad]
        merged = ebd.merge(dd, on="_k", suffixes=("_e", "_d"))
        print("E∩D 异常键=%d  merge 命中=%d" % (len(badk), len(merged)))
        if len(merged):
            for side in ("e", "d"):
                vol = pd.to_numeric(merged["vol_%s" % side],
                                    errors="coerce").to_numpy(dtype="float64")
                rate = pd.to_numeric(merged["%s_%s" % (RC, side)],
                                     errors="coerce").to_numpy(dtype="float64")
                den = pd.to_numeric(merged["%s_%s" % (DEN, side)],
                                    errors="coerce").to_numpy(dtype="float64")
                o = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
                     & np.isfinite(den) & (den > 0) & (rate > 0))
                rr = rate[o] / (vol[o] / den[o])
                print("  %s 侧: n=%d  ratio≈1=%d  ratio≈100=%d"
                      % (side.upper(), int(o.sum()),
                         int((np.abs(rr - 1.0) <= TOL).sum()),
                         int((np.abs(rr - 100.0) <= TOL * 100).sum())))
            print("  样例键: %s" % list(badk)[:5])
            print("  样例明细:")
            for c in merged.columns:
                print("    %-22s %s" % (c, merged[c].head(3).tolist()))

    print("\n" + "=" * 78)
    print("D. 文件指纹（确认两次读的是同一份数据）")
    print("=" * 78)
    for p in (D_PATH, E_PATH):
        st = __import__("os").stat(p)
        print("  %-70s size=%d mtime=%s"
              % (p, st.st_size,
                 __import__("datetime").datetime.fromtimestamp(
                     st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")))

    print("\nRATE100_RECON_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())