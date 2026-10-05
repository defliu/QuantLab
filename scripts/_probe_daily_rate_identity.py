# coding: utf-8
"""用「反解隐含分母」定死 D/E 日线换手率口径（只读）。

起因：_probe_daily_units.py 显示
    turnover_rate   == vol/float_share  仅  931216/14645649  (6.4%) 成立
    turnover_rate_f == vol/free_share   仅 1304507/14631561  (8.9%) 成立
说明「vol/某个股本列」这个公式本身在大多数行上不成立，之前挑分母的做法是错的。
用错分母去比中位数，得到的 median(vol/expect)=1.59 是公式错配的产物，
不能用来判定 E 新行的量纲对错。

正确做法：反解。对每行直接算
    implied_den = vol / turnover_rate       （该行实际使用的分母）
然后回答两个问题：
  Q1 行级：implied_den 等于哪个候选股本列的哪一档倍数？
  Q2 列级（决定性证据）：同一只股票跨多个交易日的 implied_den 是否稳定？
     若 turnover_rate = vol / X 且 X 是真实股本，则同一股票 implied_den 应与
     候选列同向、同量级；反过来，若 implied_den 与候选列严重失配而自身很稳，
     说明真实分母是别的股本口径（自由流通/自由流通股等），不在候选列里。
Q2 比 Q1 更强：股本类变量逐日几乎不变，所以「逐日稳定 + 与哪一列数值一致」
     才是能唯一定位口径的证据。

只做统计，不写任何文件。
"""
import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

RATE_COLS = ["turnover_rate", "turnover_rate_f"]
DEN_COLS = ["total_share", "float_share", "free_share"]
SCALES = [("x1", 1.0), ("x100", 100.0), ("x0.01", 0.01)]
# 命中阈值：换手率落盘只有有限小数位，逐档放宽看哪个假设能解释数据
TOLS = [1e-6, 1e-4, 1e-3, 1e-2]


def code_map(*series_list):
    """构造跨表统一的 ts_code -> int 映射。

    必须显式建全局映射：pd.factorize 对每张表独立编号，D 的 0 号和 E 的 0 号
    不是同一只股票。若各自 factorize 再拼成 date*10000+code，复合键跨表不可比，
    np.isin 会匹配出大量假命中（曾导致 E-only 误算成 53300 行 / 跨到 2026-01-06，
    与已确认的 50052 行 / 20260914~20260924 矛盾）。
    """
    vals = set()
    for s in series_list:
        vals.update(s.astype(str).unique().tolist())
    cats = sorted(vals)
    dtype = pd.CategoricalDtype(categories=cats)
    return dtype


def attach_keys(df, dtype, with_date):
    td = (df["trade_date"].astype(str).str.replace("-", "", regex=False)
          .astype("int64"))
    # 注意：Pandas 3.x 下 astype(CategoricalDtype) 返回 Series，不能取 .codes，
    # 必须显式构造 Categorical 才能拿 codes。
    code = pd.Categorical(df["ts_code"].astype(str), dtype=dtype).codes
    code = np.asarray(code, dtype="int64")
    df["_code"] = code
    df["_key"] = td.to_numpy(dtype="int64") * 10000 + code
    if not with_date:
        df["_tdn"] = td
    return df


def prep(path, need_date=True):
    """按「先读值列，键从索引或列补齐」的顺序加载。

    D 盘键在 MultiIndex 里（trade_date/ts_code 不在普通 columns），
    直接按列名读再 reset_index 会撞 'cannot insert trade_date, already exists'；
    E 盘是 RangeIndex，键在普通列里。所以统一先只读值列，再分别补键。
    """
    value_cols = ["vol"] + RATE_COLS + DEN_COLS
    df = pd.read_parquet(path, columns=value_cols)
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
    elif need_date:
        keys = pd.read_parquet(path, columns=["trade_date", "ts_code"])
        df = pd.concat([keys.reset_index(drop=True),
                        df.reset_index(drop=True)], axis=1)
    return df


def identity(tag, df):
    """Q1 行级：implied_den 与各候选列的比值命中统计。"""
    vol = pd.to_numeric(df["vol"], errors="coerce").to_numpy(dtype="float64")

    for rc in RATE_COLS:
        if rc not in df.columns:
            continue
        rate = pd.to_numeric(df[rc], errors="coerce").to_numpy(dtype="float64")
        ok = np.isfinite(vol) & np.isfinite(rate) & (vol > 0) & (rate > 0)
        n_ok = int(ok.sum())
        if n_ok == 0:
            print("  %-16s 无可用行" % rc)
            continue

        # 关键修复：先算全长的 implied（无效行置 NaN），再按同一掩码切片，
        # 保证 implied 与 den 的索引完全对齐（原先在此处 IndexError）
        implied = np.full(len(df), np.nan, dtype="float64")
        implied[ok] = vol[ok] / rate[ok]

        p = np.percentile(implied[ok], [1, 50, 99])
        print("  %-16s 可用行=%-9d implied_den p1/p50/p99=%.4f/%.4f/%.4f"
              % (rc, n_ok, p[0], p[1], p[2]))

        for dc in DEN_COLS:
            if dc not in df.columns:
                continue
            den = pd.to_numeric(df[dc], errors="coerce").to_numpy(dtype="float64")
            m = ok & np.isfinite(den) & (den > 0)
            if int(m.sum()) == 0:
                print("      vs %-12s 无可比行" % dc)
                continue

            ratio = implied[m] / den[m]
            bits = []
            for label, f in SCALES:
                dev = np.abs(ratio * f - 1.0)
                # 逐档放宽：越宽松命中越多，说明该假设越能解释数据
                tier = "/".join("%d(%.1f%%)"
                                % (int((dev <= t).sum()),
                                   100.0 * (dev <= t).sum() / len(ratio))
                                for t in TOLS)
                bits.append("%s %s" % (label, tier))
            print("      vs %-12s n=%-8d ratio p50=%.4f  [%s]"
                  % (dc, len(ratio), float(np.median(ratio)),
                     " | ".join(bits)))


def cross_section(tag, df, top_codes=200):
    """Q2 列级决定性证据：同公司跨日 implied_den 的稳定性 + 与候选列的相关性。

    股本类变量逐日近乎不变，所以：
      · implied_den 组内变异系数极小 -> 分母确实是某种股本口径
      · 组内中位数与某候选列组内中位数一致 -> 锁定是哪一档股本
    """
    print("  --- %s ---" % tag)
    need = ["ts_code", "vol"] + RATE_COLS + DEN_COLS
    have = [c for c in need if c in df.columns]
    sub = df[have]

    counts = sub["ts_code"].value_counts()
    keep = set(counts.head(top_codes).index)
    sub = sub[sub["ts_code"].isin(keep)]

    for rc in RATE_COLS:
        if rc not in sub.columns:
            continue
        g = sub.groupby("ts_code", sort=False)
        vol = pd.to_numeric(sub["vol"], errors="coerce")
        rate = pd.to_numeric(sub[rc], errors="coerce")
        mask = vol.notna() & rate.notna() & (vol > 0) & (rate > 0)
        imp = pd.Series(np.nan, index=sub.index, dtype="float64")
        imp[mask] = (vol[mask] / rate[mask]).to_numpy(dtype="float64")
        sub = sub.assign(_imp=imp)
        g = sub.groupby("ts_code", sort=False)

        med = g["_imp"].median()
        std = g["_imp"].std()
        nday = g["_imp"].count()
        live = med.notna() & (nday >= 5)
        if int(live.sum()) < 10:
            print("    %-16s 有效公司不足，跳过" % rc)
            continue

        cv = (std[live] / med[live]).abs()
        print("    %-16s 公司数=%-5d 组内CV p50=%.6f p90=%.6f  (越接近0越像股本口径)"
              % (rc, int(live.sum()), float(cv.median()),
                 float(cv.quantile(0.90))))

        for dc in DEN_COLS:
            if dc not in sub.columns:
                continue
            dnum = pd.to_numeric(sub[dc], errors="coerce")
            dmed = dnum.groupby(sub["ts_code"], sort=False).median()
            dlive = dmed.notna() & dmed.gt(0) & live
            if int(dlive.sum()) < 10:
                continue
            r = (med[dlive] / dmed[dlive])
            bits = []
            for label, f in SCALES:
                dev = np.abs(r.to_numpy() * f - 1.0)
                bits.append("%s %.1f%%" % (label,
                                           100.0 * (dev <= 1e-3).mean()))
            print("        vs %-12s n=%-5d 公司级 ratio p50=%.4f  命中@1e-3: %s"
                  % (dc, int(dlive.sum()), float(r.median()),
                     " | ".join(bits)))


def main():
    # 关键：必须先把 D、E 都读出来再建统一 code 映射，最后才算 E-only。
    # 若先算 D 的键、E 的键各自独立 factorize，跨表不可比，会静默产生假 E-only。
    d = prep(D_PATH, need_date=True)
    e = prep(E_PATH, need_date=True)
    dtype = code_map(d["ts_code"], e["ts_code"])
    d = attach_keys(d, dtype, with_date=True)
    e = attach_keys(e, dtype, with_date=True)

    print("=" * 78)
    print("A. D 盘全历史 —— 口径基准")
    print("=" * 78)
    print("[D] rows=%d dates=%s ~ %s"
          % (len(d), d["trade_date"].min(), d["trade_date"].max()))
    identity("D all", d)

    print("\n" + "=" * 78)
    print("B. D 盘近期（20260801 之后）—— 口径是否随时间漂移")
    print("=" * 78)
    recent = d[d["_key"] // 10000 >= 20260801]
    print("[D recent] rows=%d" % len(recent))
    identity("D recent", recent)

    print("\n" + "=" * 78)
    print("C. E 独有行 —— 拟追加区间")
    print("=" * 78)
    d_keys = np.unique(d["_key"].to_numpy())
    eo = e[~np.isin(e["_key"].to_numpy(), d_keys)].copy()
    print("[E only] rows=%d dates=%s ~ %s codes=%d"
          % (len(eo), eo["trade_date"].min(), eo["trade_date"].max(),
             eo["ts_code"].nunique()))

    # 自检：E-only 必须完全落在 E 的最大日期区间内，且行数与键空间一致。
    # 若 code 映射错乱，这里会立刻炸掉，而不是静默给出错误的 E-only。
    d_codes = set(d["ts_code"].unique())
    e_codes = set(e["ts_code"].unique())
    dmax = int(d["_key"].max() // 10000)
    emax = int(e["_key"].max() // 10000)
    if not (dmax < emax):
        print("  !! 自检失败：D 最大日期 %d 未早于 E 最大日期 %d" % (dmax, emax))
        return 2
    if int(eo["_key"].max() // 10000) != emax:
        print("  !! 自检失败：E-only 未触及 E 最大交易日")
        return 2
    print("[自检] D codes=%d E codes=%d E独有codes=%d E-only日期=%s"
          % (len(d_codes), len(e_codes), eo["ts_code"].nunique(),
             sorted(set((eo["_key"] // 10000).tolist()))))

    identity("E only", eo)

    print("\n" + "=" * 78)
    print("D. 跨公司一致性（决定性证据）")
    print("=" * 78)
    print("  [D recent]")
    cross_section("D recent", recent)
    print("  [E only]")
    cross_section("E only", eo)

    print("\n" + "=" * 78)
    print("E. 分布对照：D 末日 vs E 独有行")
    print("=" * 78)
    d_last = int(d["_key"].max() // 10000)
    frames = [("D last day", d[d["_key"] // 10000 == d_last]), ("E only", eo)]
    for tag, frame in frames:
        if len(frame) == 0:
            continue
        vol = pd.to_numeric(frame["vol"], errors="coerce").to_numpy(dtype="float64")
        for rc in RATE_COLS:
            if rc not in frame.columns:
                continue
            rate = pd.to_numeric(frame[rc], errors="coerce").to_numpy(dtype="float64")
            ok = np.isfinite(vol) & np.isfinite(rate) & (vol > 0) & (rate > 0)
            if int(ok.sum()) == 0:
                continue
            imp = vol[ok] / rate[ok]
            p = np.percentile(imp, [1, 50, 99])
            print("  %-10s %-16s n=%-7d implied_den p1/p50/p99=%.4f/%.4f/%.4f"
                  % (tag, rc, len(imp), p[0], p[1], p[2]))

    print("\nRATE_IDENTITY_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())