# coding: utf-8
"""判定 3 个缺陷日到底哪一侧权威（只读、低内存）。

背景：
- verify2 的 A4 用 isclose(x-1.0, 0.0, rtol=1e-3, atol=0.0)，
  以 0 为比较基准时 rtol 失效、atol=0 等价于严格相等，
  报出的约 65% 是脚本缺陷而非数据缺陷；本脚本用正确写法重算。
- A5 暴露更严重的问题：3 个缺陷日上 D/E 除价格量列外，
  volume_ratio 一致率约 0.04%、pb 约 6.98%、dv_ratio 约 4.79%，
  疑似 E 这 3 天是「坏批次」，不止换手率一列坏。

判定方法：内部自洽性检验（不依赖另一侧数据）
  change          = close - pre_close
  pct_chg         = close/pre_close - 1
  total_mv        = close * total_share
  circ_mv         = close * float_share
  turnover_rate   = vol/float_share
  turnover_rate_f = vol/free_share
  up_limit        ~ pre_close * 1.1（10% 板，0.01 元取整）
  down_limit      ~ pre_close * 0.9
哪一侧自洽率高，哪一侧权威。正常邻近日与 E-only 新行作为基准线。

内存策略：不用 set(str) 存 1400 万键，改用 hash + np.isin；
D 侧只全量读一次并立刻裁剪到 8 个交易日；NaN 体检逐列读。
只统计，不写任何文件。
"""
import gc

import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

BAD_DATES = ["20260820", "20260825", "20260827"]
REF_DATES = ["20260819", "20260821", "20260824", "20260826", "20260828"]
KEEP_DATES = BAD_DATES + REF_DATES

CHECK_COLS = [
    "vol", "close", "pre_close", "change", "pct_chg",
    "total_share", "float_share", "free_share",
    "total_mv", "circ_mv", "turnover_rate", "turnover_rate_f",
    "up_limit", "down_limit",
]


def td8(s):
    """日期归一化为 YYYYMMDD 字符串。"""
    return s.astype(str).str.slice(0, 10).str.replace("-", "", regex=False)


def num(df, c):
    return pd.to_numeric(df[c], errors="coerce").to_numpy("float64")


def ratio_hit(hit, tot):
    return "%d/%d=%.1f%%" % (hit, tot, 100.0 * hit / max(1, tot))


def selfcheck(s):
    """返回 [(指标名, 命中数, 可用数)]，只用该行子集自身的算术。"""
    close = num(s, "close")
    pre = num(s, "pre_close")
    vol = num(s, "vol")
    chg = num(s, "change")
    pct = num(s, "pct_chg")
    tsh = num(s, "total_share")
    fsh = num(s, "float_share")
    frs = num(s, "free_share")
    tmv = num(s, "total_mv")
    cmv = num(s, "circ_mv")
    tr = num(s, "turnover_rate")
    trf = num(s, "turnover_rate_f")
    up = num(s, "up_limit")
    dn = num(s, "down_limit")

    def pair(calc, ref, rtol, atol):
        ok = np.isfinite(calc) & np.isfinite(ref)
        if not ok.any():
            return 0, 0
        return int(np.isclose(calc[ok], ref[ok], rtol=rtol,
                              atol=atol).sum()), int(ok.sum())

    out = [("change=close-pre_close", *pair(close - pre, chg, 1e-6, 1e-6))]

    h, n = pair((close - pre) / np.where(pre == 0, np.nan, pre) * 100.0,
                pct, 1e-4, 1e-4)
    out.append(("pct_chg=close/pre-1", h, n))

    h, n = pair(close * np.where(tsh > 0, tsh, np.nan), tmv, 2e-3, 1.0)
    out.append(("total_mv=close*total_sh", h, n))

    h, n = pair(close * np.where(fsh > 0, fsh, np.nan), cmv, 2e-3, 1.0)
    out.append(("circ_mv=close*float_sh", h, n))

    ok = np.isfinite(vol) & np.isfinite(fsh) & (vol > 0) & (fsh > 0)
    h = int(np.isclose(vol[ok] / fsh[ok], tr[ok], rtol=1e-3,
                       atol=1e-6).sum()) if ok.any() else 0
    out.append(("tr=vol/float_share", h, int(ok.sum())))

    ok = np.isfinite(vol) & np.isfinite(frs) & (vol > 0) & (frs > 0)
    h = int(np.isclose(vol[ok] / frs[ok], trf[ok], rtol=1e-3,
                       atol=1e-6).sum()) if ok.any() else 0
    out.append(("trf=vol/free_share", h, int(ok.sum())))

    ok = np.isfinite(pre) & np.isfinite(up) & (pre > 0)
    h = int(np.isclose(up[ok], np.round(pre[ok] * 1.1, 2), rtol=6e-3,
                       atol=0.011).sum()) if ok.any() else 0
    out.append(("up_limit~pre*1.1", h, int(ok.sum())))

    ok = np.isfinite(pre) & np.isfinite(dn) & (pre > 0)
    h = int(np.isclose(dn[ok], np.round(pre[ok] * 0.9, 2), rtol=6e-3,
                       atol=0.011).sum()) if ok.any() else 0
    out.append(("down_limit~pre*0.9", h, int(ok.sum())))

    assert len(out) == 8
    return out


def print_side_by_side(dmap, emap, dates, title):
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)
    for dt in dates:
        dres = dmap.get(dt)
        eres = emap.get(dt)
        drows = dres[0] if dres else 0
        erows = eres[0] if eres else 0
        print("\n### %s   D=%d rows   E=%d rows" % (dt, drows, erows))
        print("  %-24s %18s %18s" % ("指标", "D", "E"))
        for i in range(8):
            name = (dres[1][i][0] if dres else CHECK_NAMES[i])
            ds = ratio_hit(*dres[1][i][1:]) if dres else "-"
            es = ratio_hit(*eres[1][i][1:]) if eres else "-"
            print("  %-24s %18s %18s" % (name, ds, es))


CHECK_NAMES = [
    "change=close-pre_close", "pct_chg=close/pre-1",
    "total_mv=close*total_sh", "circ_mv=close*float_sh",
    "tr=vol/float_share", "trf=vol/free_share",
    "up_limit~pre*1.1", "down_limit~pre*0.9",
]


def main():
    print("=" * 96)
    print("STEP1 键空间（hash 匹配，避免 1400 万字符串 set）")
    print("=" * 96)
    ek = pd.read_parquet(E_PATH, columns=["trade_date", "ts_code"])
    e_td = td8(ek["trade_date"])
    e_h = pd.util.hash_pandas_object(
        pd.DataFrame({"a": e_td.to_numpy(),
                      "b": ek["ts_code"].astype(str).to_numpy()}),
        index=False).to_numpy("uint64")
    e_td_all = e_td.to_numpy()
    e_code_all = ek["ts_code"].astype(str).to_numpy()
    del ek, e_td
    gc.collect()

    di = pd.read_parquet(D_PATH, columns=[]).index
    d_td_all = td8(pd.Series(di.get_level_values("trade_date"))).to_numpy()
    d_code_all = pd.Index(di.get_level_values("ts_code")).astype(str).to_numpy()
    del di
    d_h = pd.util.hash_pandas_object(
        pd.DataFrame({"a": d_td_all, "b": d_code_all}),
        index=False).to_numpy("uint64")
    del d_td_all, d_code_all
    gc.collect()

    in_d = np.isin(e_h, d_h)
    del e_h, d_h
    gc.collect()
    print("E rows      = %d" % len(in_d))
    print("common(E∩D) = %d" % int(in_d.sum()))
    print("E-only rows = %d" % int((~in_d).sum()))

    print("\n" + "=" * 96)
    print("STEP2 D 侧：8 个交易日内部自洽性")
    print("=" * 96)
    dd = pd.read_parquet(D_PATH, columns=CHECK_COLS)
    d_td = td8(pd.Series(dd.index.get_level_values("trade_date"))).to_numpy()
    dmap = {}
    for dt in KEEP_DATES:
        sub = dd[d_td == dt]
        dmap[dt] = (len(sub), selfcheck(sub))
        del sub
    del dd, d_td
    gc.collect()

    print("  已裁剪并释放 D 全表")

    print("\n" + "=" * 96)
    print("STEP3 E 侧：8 个交易日 + E-only 内部自洽性")
    print("=" * 96)
    ee = pd.read_parquet(E_PATH, columns=CHECK_COLS)
    ee_td = td8(pd.Series(e_td_all)).to_numpy()
    emap = {}
    for dt in KEEP_DATES:
        sub = ee[ee_td == dt]
        emap[dt] = (len(sub), selfcheck(sub))
        del sub
    eo = ee[~in_d]
    eo_res = (len(eo), selfcheck(eo))
    del ee, ee_td
    gc.collect()
    print("  E-only 已体检：%d rows" % eo_res[0])

    print_side_by_side(dmap, emap, BAD_DATES,
                       "STEP4 结论区：3 个缺陷日 D vs E 内部自洽率")
    print_side_by_side(dmap, emap, REF_DATES,
                       "STEP5 对照区：正常邻近日（基准线）")

    print("\n" + "=" * 96)
    print("STEP6 E-only 新行内部自洽性（拟追加批次的质量基线）")
    print("=" * 96)
    eo_dates = sorted(set(e_td_all[~in_d].tolist()))
    print("  行=%d  交易日=%d 个  范围=%s ~ %s"
          % (eo_res[0], len(eo_dates), eo_dates[0], eo_dates[-1]))
    print("  日期明细=%s" % ",".join(eo_dates))
    for r in eo_res[1]:
        print("  %-24s %s" % (r[0], ratio_hit(r[1], r[2])))

    print("\n" + "=" * 96)
    print("STEP7 列集合与 NaN 密度")
    print("=" * 96)
    import pyarrow.parquet as pq
    d_schema = pq.ParquetFile(D_PATH).schema_arrow.names
    e_schema = pq.ParquetFile(E_PATH).schema_arrow.names
    print("  D 列数=%d  E 列数=%d" % (len(d_schema), len(e_schema)))
    print("  仅 E 有=%s" % ([c for c in e_schema if c not in d_schema] or "无"))
    print("  仅 D 有=%s" % ([c for c in d_schema if c not in e_schema] or "无"))

    eo_pos = np.flatnonzero(~in_d)
    del in_d, e_td_all, e_code_all
    gc.collect()

    print("\n  %-22s %12s %12s %10s" % ("列", "E-only空值率", "D全表空值率", "差异"))
    for c in e_schema:
        if c in ("trade_date", "ts_code"):
            continue
        se = pd.read_parquet(E_PATH, columns=[c])[c]
        a = float(se.isna().to_numpy()[eo_pos].mean())
        del se
        sd = pd.read_parquet(D_PATH, columns=[c])[c]
        b = float(sd.isna().mean())
        del sd
        gc.collect()
        flag = "  <<<" if abs(a - b) > 0.02 else ""
        print("  %-22s %11.2f%% %11.2f%% %9.2f%%%s"
              % (c, 100 * a, 100 * b, 100 * (a - b), flag))

    print("\nAUTHORITY_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
