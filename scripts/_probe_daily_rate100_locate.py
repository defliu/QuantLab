# coding: utf-8
"""定位 E 中「换手率多除 100」缺陷行的确切范围（只读）。

口径已定死（_probe_daily_rate_identity.py）：
    turnover_rate   = vol / float_share
    turnover_rate_f = vol / free_share
定义 ratio = 落盘值 / 按上式重算值，则
    ratio ≈ 1     正常
    ratio ≈ 0.01  落盘值被额外除了 100（量纲缺陷）
    ratio ≈ 100   落盘值被额外乘了 100

注意方向：此前 _probe_daily_rate100.py 把 ratio≈0.01 标成「少除100」是标签写反
（它用 |ratio*f - 1| <= tol 判定，f=100 命中对应 ratio≈0.01），
后续两个 scope/recon 脚本又只查了 ratio≈100 方向，故报 0。
本脚本直接按 ratio 本身判定两个方向。

本脚本输出三件事：
  1. 缺陷行数与分区归属（E∩D / E-only）
  2. 缺陷行的日期与股票分布
  3. 缺陷键上 D 侧同键是否正常（决定修复方向：保留 D 还是必须修 E）

只统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

RATE_DEN = {"turnover_rate": "float_share",
            "turnover_rate_f": "free_share"}
TOL = 1e-3

BODY_COLS = ["vol"] + list(RATE_DEN.values()) + list(RATE_DEN.keys())


def norm_td(s):
    return s.astype(str).str.replace("-", "", regex=False)


def attach_key(df, td_series, code_series):
    df["trade_date"] = norm_td(td_series)
    df["ts_code"] = code_series.astype(str)
    df["_k"] = df["trade_date"] + "\u0001" + df["ts_code"]
    return df


def ratio_vec(df, rc, den_col):
    vol = pd.to_numeric(df["vol"], errors="coerce").to_numpy(dtype="float64")
    rate = pd.to_numeric(df[rc], errors="coerce").to_numpy(dtype="float64")
    den = pd.to_numeric(df[den_col], errors="coerce").to_numpy(dtype="float64")
    ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
          & np.isfinite(den) & (den > 0) & (rate > 0))
    out = np.full(len(df), np.nan, dtype="float64")
    out[ok] = rate[ok] / (vol[ok] / den[ok])
    return out, ok


def band(r, ok, target, rel):
    return ok & (np.abs(r - target) <= TOL * rel)


def main():
    e = pd.read_parquet(E_PATH, columns=["trade_date", "ts_code"] + BODY_COLS)
    e = attach_key(e, e["trade_date"], e["ts_code"])

    d = pd.read_parquet(D_PATH, columns=BODY_COLS)
    d = attach_key(d, d.index.get_level_values("trade_date"),
                   d.index.get_level_values("ts_code"))

    in_d = e["_k"].isin(set(d["_k"].tolist())).to_numpy()
    print("=" * 78)
    print("分区: E∩D=%d  E-only=%d" % (int(in_d.sum()), int((~in_d).sum())))

    print("\n" + "=" * 78)
    print("E 全表 换手率倍率分布（按 ratio 本身判定）")
    print("=" * 78)

    all_bad_keys = set()
    for rc, den_col in RATE_DEN.items():
        r, ok = ratio_vec(e, rc, den_col)
        low = band(r, ok, 0.01, 0.01)
        high = band(r, ok, 100.0, 100.0)
        norm = band(r, ok, 1.0, 1.0)
        print("\n字段 %s （分母 %s）" % (rc, den_col))
        print("  可用行=%d" % int(ok.sum()))
        print("  ratio≈1    (正常)      =%-8d E∩D=%-8d E-only=%d"
              % (int(norm.sum()), int((norm & in_d).sum()),
                 int((norm & ~in_d).sum())))
        print("  ratio≈0.01 (多除100)   =%-8d E∩D=%-8d E-only=%d"
              % (int(low.sum()), int((low & in_d).sum()),
                 int((low & ~in_d).sum())))
        print("  ratio≈100  (少除100)   =%-8d E∩D=%-8d E-only=%d"
              % (int(high.sum()), int((high & in_d).sum()),
                 int((high & ~in_d).sum())))

        if int(low.sum()):
            sub = e.loc[low]
            uq, cnt = np.unique(sub["trade_date"].to_numpy(),
                                return_counts=True)
            order = np.argsort(-cnt)
            print("  缺陷日期分布 (共 %d 个交易日):" % len(uq))
            for i in order[:20]:
                print("    %s = %d" % (uq[i], cnt[i]))
            print("  涉及股票=%d" % sub["ts_code"].nunique())
            all_bad_keys.update(sub["_k"].tolist())

    if not all_bad_keys:
        print("\n结论: E 未发现换手率量纲缺陷。")
        print("RATE100_LOCATE_DONE")
        return 0

    print("\n" + "=" * 78)
    print("D 侧同键表现（决定修复方向）")
    print("=" * 78)
    de = e[e["_k"].isin(all_bad_keys)]
    dd = d[d["_k"].isin(all_bad_keys)]
    m = de.merge(dd, on="_k", suffixes=("_e", "_d"))
    print("缺陷键=%d  E侧行=%d  D侧行=%d  merge=%d"
          % (len(all_bad_keys), len(de), len(dd), len(m)))

    for rc, den_col in RATE_DEN.items():
        e_side = m.assign(vol=m["vol_e"], **{den_col: m["%s_e" % den_col],
                                            rc: m["%s_e" % rc]})
        d_side = m.assign(vol=m["vol_d"], **{den_col: m["%s_d" % den_col],
                                            rc: m["%s_d" % rc]})
        r, ok = ratio_vec(e_side, rc, den_col)
        rd, okd = ratio_vec(d_side, rc, den_col)
        print("\n  字段 %s" % rc)
        print("    E 侧 ratio≈1=%-6d ratio≈0.01=%-6d (可用=%d)"
              % (int(band(r, ok, 1.0, 1.0).sum()),
                 int(band(r, ok, 0.01, 0.01).sum()), int(ok.sum())))
        print("    D 侧 ratio≈1=%-6d ratio≈0.01=%-6d (可用=%d)"
              % (int(band(rd, okd, 1.0, 1.0).sum()),
                 int(band(rd, okd, 0.01, 0.01).sum()), int(okd.sum())))
        same = np.isclose(
            pd.to_numeric(m["%s_e" % rc], errors="coerce").to_numpy("float64"),
            pd.to_numeric(m["%s_d" % rc], errors="coerce").to_numpy("float64"),
            rtol=1e-9, atol=0.0, equal_nan=True,
        )
        print("    D 与 E 落盘值相同=%d/%d -> 修复方向: %s"
              % (int(np.nansum(same)), len(m),
                 "保留 D 即可，无需修 E" if int(np.nansum(same)) == len(m)
                 else "必须修 E 或逐键裁决"))

        vclose = np.isclose(
            m["vol_e"].to_numpy("float64"),
            m["vol_d"].to_numpy("float64"),
            rtol=1e-9, atol=0.0, equal_nan=True,
        )
        print("    D 与 E vol 相同=%d/%d  float_share 相同=%d/%d"
              % (int(vclose.sum()), len(m),
                 int(np.isclose(m["float_share_e"].to_numpy("float64"),
                                m["float_share_d"].to_numpy("float64"),
                                rtol=1e-9, atol=0.0,
                                equal_nan=True).sum()), len(m)))

    print("\n样例（前 10 行，E 侧 vs D 侧原值）:")
    show = ["trade_date_e", "ts_code_e", "vol_e", "vol_d",
            "float_share_e", "float_share_d",
            "turnover_rate_e", "turnover_rate_d"]
    print(m[show].head(10).to_string(index=False))

    print("\nRATE100_LOCATE_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())