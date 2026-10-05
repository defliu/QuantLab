# coding: utf-8
"""下钻 E 全表 ratio≈100 的 16634 行 —— 是 D 的错还是 E 的错（只读）。

上一步发现：E 全表 turnover_rate / turnover_rate_f 各有约 1.66 万行
满足「落盘值 ≈ 重算值 * 100」，即落盘值比按 float_share/free_share 重算的
值大 100 倍。方向是「少除 100」而非先前假设的「多除 100」。

关键问题：这类行是
  (a) E 单方的量纲错误，
  (b) D 的错、E 是对的（那合并时应采信 E），
  (c) 两边同错（分母口径本身另有分支，如按手计的股本列），
  (c') 或者这批行分母列本身异常（float_share/free_share 落在另一档）。

判定方法：对每个缺陷键，同时算
    D 的落盘值 vs D 的重算值
    E 的落盘值 vs E 的重算值
    D 与 E 落盘值之比
若 D 侧自洽（ratio≈1）而 E 侧不自洽 -> E 错，追加/替换都必须修正 E。
若两侧都自洽但互不相等 -> 分母口径分歧，需进一步定位。
若 D 侧也不自洽 -> 说明该批行的分母列有其他来源。

只统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

RATE_DEN = {"turnover_rate": "float_share",
            "turnover_rate_f": "free_share"}
TOL = 1e-3


def norm_td(s):
    return s.astype(str).str.replace("-", "", regex=False)


def load(path, from_e):
    cols = ["vol"] + list(RATE_DEN.values()) + list(RATE_DEN.keys())
    if from_e:
        df = pd.read_parquet(path, columns=["trade_date", "ts_code"] + cols)
        df["trade_date"] = norm_td(df["trade_date"])
    else:
        keys = pd.read_parquet(path, columns=["trade_date", "ts_code"])
        if isinstance(keys.index, pd.MultiIndex):
            keys = keys.reset_index()
        body = pd.read_parquet(path, columns=cols)
        df = pd.concat([keys.reset_index(drop=True),
                        body.reset_index(drop=True)], axis=1)
        df["trade_date"] = norm_td(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    df["_k"] = df["trade_date"] + "\u0001" + df["ts_code"]
    return df


def self_ratio(df, rc, den_col):
    vol = pd.to_numeric(df["vol"], errors="coerce").to_numpy(dtype="float64")
    rate = pd.to_numeric(df[rc], errors="coerce").to_numpy(dtype="float64")
    den = pd.to_numeric(df[den_col], errors="coerce").to_numpy(dtype="float64")
    ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
          & np.isfinite(den) & (den > 0) & (rate > 0))
    out = np.full(len(df), np.nan, dtype="float64")
    out[ok] = rate[ok] / (vol[ok] / den[ok])
    return out


def main():
    d = load(D_PATH, from_e=False)
    e = load(E_PATH, from_e=True)

    d_idx = d.set_index("_k")
    e_idx = e.set_index("_k")
    common = d_idx.index.intersection(e_idx.index)
    print("common keys=%d" % len(common))

    dc = d_idx.loc[common]
    ec = e_idx.loc[common]

    for rc, den_col in RATE_DEN.items():
        print("\n" + "=" * 78)
        print("字段 %s （分母列 %s）" % (rc, den_col))
        print("=" * 78)

        rd = self_ratio(dc, rc, den_col)
        re = self_ratio(ec, rc, den_col)
        vd = pd.to_numeric(dc[rc], errors="coerce").to_numpy(dtype="float64")
        ve = pd.to_numeric(ec[rc], errors="coerce").to_numpy(dtype="float64")

        ok = np.isfinite(rd) & np.isfinite(re) & np.isfinite(vd) & np.isfinite(ve)
        d_self1 = ok & (np.abs(rd - 1.0) <= TOL)
        e_self1 = ok & (np.abs(re - 1.0) <= TOL)
        d_self100 = ok & (np.abs(rd - 100.0) <= TOL * 100)
        e_self100 = ok & (np.abs(re - 100.0) <= TOL * 100)

        print("共有键可用行=%d" % int(ok.sum()))
        print("  D 自洽 ratio≈1   : %d (%.3f%%)"
              % (int(d_self1.sum()), 100.0 * int(d_self1.sum()) / max(1, int(ok.sum()))))
        print("  E 自洽 ratio≈1   : %d (%.3f%%)"
              % (int(e_self1.sum()), 100.0 * int(e_self1.sum()) / max(1, int(ok.sum()))))
        print("  D ratio≈100      : %d" % int(d_self100.sum()))
        print("  E ratio≈100      : %d" % int(e_self100.sum()))

        bad_e = ok & e_self100
        n_bad = int(bad_e.sum())
        print("  -> E ratio≈100 行 = %d" % n_bad)
        if n_bad == 0:
            continue

        sub = bad_e
        print("     其中 D 自洽(ratio≈1) = %d  (%.1f%%)"
              % (int((sub & d_self1).sum()),
                 100.0 * int((sub & d_self1).sum()) / n_bad))
        print("     其中 D 也≈100      = %d"
              % int((sub & d_self100).sum()))

        # 落盘值直接比对：D 与 E 在这些键上是否一致
        same = np.abs(vd[sub] - ve[sub]) <= 1e-9
        print("     D落盘值 == E落盘值 = %d (%.1f%%)"
              % (int(same.sum()), 100.0 * int(same.sum()) / n_bad))

        dates = dc["trade_date"].to_numpy()[sub]
        codes = dc["ts_code"].to_numpy()[sub]
        uq, cnt = np.unique(dates, return_counts=True)
        top = sorted(zip(cnt, uq), reverse=True)[:12]
        print("     涉及股票=%d  日期数=%d" % (len(set(codes.tolist())), len(uq)))
        print("     日期分布 TOP: %s"
              % ", ".join("%s=%d" % (a_, b_) for b_, a_ in top))

        # 分母列在这批行上的取值特征：是否显著小于/大于全样本中位数
        den_v = pd.to_numeric(dc[den_col], errors="coerce").to_numpy(dtype="float64")
        allv = den_v[np.isfinite(den_v) & (den_v > 0)]
        print("     分母列 %s: 缺陷行p50=%.2f | 全样本p50=%.2f"
              % (den_col, float(np.nanmedian(den_v[sub])),
                 float(np.median(allv))))

    print("\nRATE100_SCOPE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())