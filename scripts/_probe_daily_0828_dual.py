# coding: utf-8
"""只跑 20260828 的 D/E 逐字段对比（闭合发现 2），并回补 C/D 部分结论打印。

用法：先跑 _probe_daily_eonly_gapfields.py（已产出 C/D），本脚本只补 E。
只读，不写任何生产文件。
"""
import gc

import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"
BAD_DATE = "20260828"


def td8(s):
    return s.astype(str).str.slice(0, 10).str.replace("-", "", regex=False)


def num(df, c):
    return pd.to_numeric(df[c], errors="coerce")


def main():
    print("=" * 100)
    print("E  %s 那 27 行：D / E 逐字段对比（闭合发现 2）" % BAD_DATE)
    print("=" * 100)
    cols = ["ts_code", "trade_date", "close", "float_share", "circ_mv",
            "total_share", "total_mv"]
    e = pd.read_parquet(E_PATH, columns=cols)
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    e28 = e[etd == BAD_DATE].copy()
    e28["ts_code"] = e28["ts_code"].astype(str)
    del e, etd
    gc.collect()

    calc = num(e28, "close") * num(e28, "float_share")
    bad = ((num(e28, "float_share") > 0)
           & np.isfinite(num(e28, "circ_mv"))
           & ~np.isclose(calc, num(e28, "circ_mv"), rtol=2e-3, atol=1.0))
    eb = e28[bad]
    print("  E 不自洽 %d 只 / 当日 %d 行" % (len(eb), len(e28)))

    d = pd.read_parquet(D_PATH, columns=cols)
    dtd = td8(pd.Series(d.index.get_level_values("trade_date"))).to_numpy()
    dcode = pd.Series(d.index.get_level_values("ts_code").astype(str)).to_numpy()
    sel = dtd == BAD_DATE
    d28 = d[sel].reset_index(drop=True)
    d28["ts_code"] = dcode[sel]
    del d, dtd, dcode
    gc.collect()

    hit = d28["ts_code"].isin(set(eb["ts_code"]))
    print("  其中 D 同日有记录的 %d 只" % int(hit.sum()))
    j = (d28[hit][["ts_code", "close", "float_share", "circ_mv",
                   "total_share", "total_mv"]]
         .merge(eb[["ts_code", "close", "float_share", "circ_mv",
                    "total_share", "total_mv"]],
                on="ts_code", suffixes=("_D", "_E")))
    if not len(j):
        print("  D 侧无对应行 → 无法对比")
        return 0

    def same(a, b, rtol=1e-6, atol=1e-4):
        return int(np.isclose(num(j, a), num(j, b), rtol=rtol, atol=atol).sum())

    print("  字段一致性（%d 行）：" % len(j))
    print("    close       %d/%d" % (same("close_E", "close_D"), len(j)))
    print("    float_share %d/%d" % (same("float_share_E", "float_share_D",
                                          atol=1e-3), len(j)))
    print("    total_share %d/%d" % (same("total_share_E", "total_share_D",
                                          atol=1e-3), len(j)))
    print("    circ_mv     %d/%d" % (same("circ_mv_E", "circ_mv_D",
                                          rtol=2e-3, atol=1.0), len(j)))
    print("    total_mv    %d/%d" % (same("total_mv_E", "total_mv_D",
                                          rtol=2e-3, atol=1.0), len(j)))

    j["fsh_rel"] = num(j, "float_share_E") / num(j, "float_share_D") - 1.0
    j["tsh_rel"] = num(j, "total_share_E") / num(j, "total_share_D") - 1.0

    print("\n  %-10s %9s %9s %13s %13s %9s %9s"
          % ("ts_code", "close_D", "close_E", "fsh_D", "fsh_E", "fsh_rel", "tsh_rel"))
    for _, r in j.sort_values("fsh_rel", key=lambda s: s.abs(),
                              ascending=False).iterrows():
        print("  %-10s %9.2f %9.2f %13.1f %13.1f %8.2f%% %8.2f%%"
              % (r["ts_code"], r["close_D"], r["close_E"],
                 r["float_share_D"], r["float_share_E"],
                 100 * r["fsh_rel"], 100 * r["tsh_rel"]))

    same_close = np.isclose(num(j, "close_E"), num(j, "close_D"), rtol=1e-6, atol=1e-4)
    same_fsh = np.isclose(num(j, "float_share_E"), num(j, "float_share_D"),
                          rtol=1e-6, atol=1e-3)
    print("\n  判读规则：")
    print("    close 全同 + float_share 有差异 → E 的股本版本错")
    print("    float_share 全同 + circ_mv 有差异 → E 的市值字段错")
    print("    两者都有差异 → E 该日整行是另一版本，绝不能覆盖 D")
    print("\n  实测：close 全同 = %s ；float_share 全同 = %s"
          % (bool(same_close.all()), bool(same_fsh.all())))
    print("\nE_ONLY_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())