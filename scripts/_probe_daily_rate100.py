# coding: utf-8
"""核实 E 的「换手率额外 /100」缺陷是否污染 E-only 新行（只读）。

背景：此前发现 E 在 20260820 对部分行的 turnover_rate / turnover_rate_f
额外除以 100。既然口径已定死为
    turnover_rate   = vol / float_share
    turnover_rate_f = vol / free_share
那么任何偏离都可用「重算值 vs 落盘值」直接量化，且不受分母猜测影响。

要回答三个问题：
  Q1 该缺陷涉及哪些日期、涉及多少行（E 全表范围）？
  Q2 E-only 新行（拟追加区间）里有没有这类行？
  Q3 若追加，缺陷行会不会落进生产库？

判据：ratio = 落盘值 / 重算值
    ratio ≈ 0.01  -> 被多除了 100（量纲错误）
    ratio ≈ 1     -> 正常
    ratio ≈ 100   -> 被少除了 100
只统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

# 已由 _probe_daily_rate_identity.py 定死的口径
RATE_DEN = {
    "turnover_rate": "float_share",
    "turnover_rate_f": "free_share",
}
SCALES = [("x0.01(多除100)", 0.01), ("x1(正常)", 1.0), ("x100(少除100)", 100.0)]
TOL = 1e-3


def load_e():
    df = pd.read_parquet(
        E_PATH,
        columns=["trade_date", "ts_code", "vol", "float_share", "free_share"]
        + list(RATE_DEN.keys()),
    )
    df["trade_date"] = (
        df["trade_date"].astype(str).str.replace("-", "", regex=False)
    )
    return df


def load_d_keys():
    keys = pd.read_parquet(D_PATH, columns=["trade_date", "ts_code"])
    if isinstance(keys.index, pd.MultiIndex):
        keys = keys.reset_index()
    keys["trade_date"] = (
        keys["trade_date"].astype(str).str.replace("-", "", regex=False)
    )
    return set((keys["trade_date"] + "\u0001" + keys["ts_code"].astype(str)).tolist())


def classify(frame, tag):
    """按「落盘值 / 重算值」把每一行归到 normal / low100 / high100。"""
    vol = pd.to_numeric(frame["vol"], errors="coerce").to_numpy(dtype="float64")
    print("\n[%s] rows=%d" % (tag, len(frame)))

    for rc, den_col in RATE_DEN.items():
        if rc not in frame.columns:
            print("  %-16s 列不存在，跳过" % rc)
            continue
        rate = pd.to_numeric(frame[rc], errors="coerce").to_numpy(dtype="float64")
        den = pd.to_numeric(frame[den_col], errors="coerce").to_numpy(dtype="float64")

        ok = (np.isfinite(vol) & np.isfinite(rate) & (vol > 0)
              & np.isfinite(den) & (den > 0) & (rate > 0))
        if int(ok.sum()) == 0:
            print("  %-16s 无可用行" % rc)
            continue

        recalc = vol[ok] / den[ok]
        ratio = rate[ok] / recalc
        n = len(ratio)

        bits = []
        for label, f in SCALES:
            hit = int((np.abs(ratio * f - 1.0) <= TOL).sum())
            bits.append("%s=%d(%.3f%%)"
                        % (label, hit, 100.0 * hit / n))
        print("  %-16s 可用行=%-8d %s"
              % (rc, n, " | ".join(bits)))

        bad = np.abs(ratio * 0.01 - 1.0) <= TOL
        if int(bad.sum()) == 0:
            print("      -> 无 /100 量纲缺陷行")
            continue

        bd = frame.loc[ok, "trade_date"].to_numpy()[bad]
        bc = frame.loc[ok, "ts_code"].to_numpy()[bad]
        uq, cnt = np.unique(bd, return_counts=True)
        top = sorted(zip(cnt, uq), reverse=True)[:10]
        print("      -> /100 缺陷行=%d (%.3f%%)  涉及股票=%d"
              % (int(bad.sum()), 100.0 * int(bad.sum()) / n,
                 len(set(bc.tolist()))))
        print("      -> 按日期: %s"
              % ", ".join("%s=%d" % (d_, c_) for c_, d_ in top))
        return_bad = {
            "rate_col": rc,
            "n_bad": int(bad.sum()),
            "dates": sorted(set(bd.tolist())),
            "codes_n": len(set(bc.tolist())),
        }
        return return_bad


def main():
    print("=" * 78)
    print("A. E 全表 —— 缺陷范围")
    print("=" * 78)
    e = load_e()
    print("[E] rows=%d dates=%s ~ %s codes=%d"
          % (len(e), e["trade_date"].min(), e["trade_date"].max(),
             e["ts_code"].nunique()))
    all_bad = classify(e, "E all")

    print("\n" + "=" * 78)
    print("B. E 与 D 的键空间")
    print("=" * 78)
    d_keys = load_d_keys()
    e["_k"] = e["trade_date"] + "\u0001" + e["ts_code"].astype(str)
    in_d = e["_k"].isin(d_keys)
    eo = e[~in_d].copy()
    print("[E-only] rows=%d  [E∩D] rows=%d" % (len(eo), int(in_d.sum())))
    print("[E-only] dates=%s"
          % sorted(set(eo["trade_date"].tolist())))

    print("\n" + "=" * 78)
    print("C. E-only 新行 —— 拟追加区间是否含缺陷")
    print("=" * 78)
    eo_bad = classify(eo, "E only")

    print("\n" + "=" * 78)
    print("D. 判定")
    print("=" * 78)
    if not eo_bad or eo_bad.get("n_bad", 0) == 0:
        print("PASS: E-only 新行无 /100 量纲缺陷，追加不会把缺陷带进生产库。")
        print("      （E 全表缺陷仅存在于与 D 重叠的历史区间，属替换决策范畴，"
              "不在追加路径上）")
        print("RATE100_DONE")
        return 0

    print("BLOCK: E-only 新行含 %d 行 /100 缺陷，涉及 %d 只股票。"
          % (eo_bad["n_bad"], eo_bad["codes_n"]))
    print("       必须在合并契约里对这些键做定点修复，否则污染生产。")
    print("       dates=%s" % eo_bad["dates"][:20])
    print("RATE100_DONE")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())