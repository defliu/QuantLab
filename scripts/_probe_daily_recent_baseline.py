# coding: utf-8
"""承接 badbatch_authority 的三个新发现，做定向下钻（只读、低内存）。

发现 1：20260825 / 20260827 的 D 侧自洽性检查全是 0/0，
        因为 total_share/float_share/free_share 整列为空 → 分母不存在，
        公式无法执行。这不等于"D 正常"，只等于"D 不可判定"。
        必须查清：D 这两天到底缺哪几列、total_mv/circ_mv 是否有值。
        若 total_mv/circ_mv 有值而仅股本空 → 属于"可从 E 回填的部分列空洞"。

发现 2：正常对照日 20260828，E 的 circ_mv=close*float_share 只有
        5520/5547=99.5%，27 行不自洽，而 D 是 100%。
        换手率倍率探针完全没覆盖这一类缺陷。必须定位这 27 行。

发现 3：badbatch_authority 的 STEP7 用"D 全表"做 NaN 基准是错的 ——
        D 覆盖 2009~2026 共 17 年、含退市股与老口径，is_st 空值率 0.00%
        是全历史聚合值；而 E-only 只有 20260914~20260924 共 9 个交易日。
        跨 17 年 vs 跨 9 天直接比空值率会得出误导结论。
        必须改成"D 近期窗口"对"E-only 窗口"的同期对比。

只统计，不写任何文件。
"""
import gc

import numpy as np
import pandas as pd

D_PATH = r"D:/astock/daily/stock_daily.parquet"
E_PATH = r"E:\temp\行情数据\个股行情全量\stock_daily_full.parquet"

BAD_DATES = ["20260820", "20260825", "20260827"]

# 近期可比窗口：E-only 起点 20260914 之前，D 侧取同样长度的自然跨度
RECENT_D_START = "20260810"
RECENT_D_END = "20260911"
EONLY_DATES = ["20260914", "20260915", "20260916", "20260917", "20260918",
               "20260921", "20260922", "20260923", "20260924"]

# 三个坏日邻近的对照日（含 20260828 —— 已发现 circ_mv 异常）
NEAR_DATES = ["20260819", "20260821", "20260824", "20260826", "20260828",
              "20260831", "20260901", "20260902"]


def td8(s):
    return s.astype(str).str.slice(0, 10).str.replace("-", "", regex=False)


def nanrate(s):
    return 100.0 * float(pd.isna(s).mean())


def find1_missing_cols():
    print("=" * 100)
    print("发现 1  D 侧 3 个坏日的列缺失全景（判定 D 是『不可用』还是『部分缺』）")
    print("=" * 100)
    cols = ["total_share", "float_share", "free_share", "turnover_rate",
            "turnover_rate_f", "total_mv", "circ_mv", "vol", "close",
            "pe_ttm", "pb", "dv_ratio", "is_st", "listed_days"]
    d = pd.read_parquet(D_PATH, columns=cols)
    dtd = td8(pd.Series(d.index.get_level_values("trade_date"))).to_numpy()
    e = pd.read_parquet(E_PATH, columns=cols + ["trade_date"])
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    print("  %-18s %10s %10s %10s %10s" % ("列", "D坏日1", "D坏日2", "D坏日3", "E坏日均值"))
    for c in cols:
        dvals = []
        for dt in BAD_DATES:
            dvals.append(nanrate(d[c][dtd == dt]))
        evals = [nanrate(e[c][etd == dt]) for dt in BAD_DATES]
        mark = "   <<< D 整列为空" if all(v > 99.9 for v in dvals) else ""
        print("  %-18s %9.2f%% %9.2f%% %9.2f%% %9.2f%%%s"
              % (c, dvals[0], dvals[1], dvals[2], float(np.mean(evals)), mark))
    print("\n  结论读法：D 整列为空的列 = 该日 D 无任何值，E 是唯一来源；")
    print("            D 非空的列 = D 有值，E 只能『补空』不能『覆盖』。")
    del d, dtd, e, etd
    gc.collect()


def find2_circmv_20260828():
    print("\n" + "=" * 100)
    print("发现 2  20260828 的 E 侧 circ_mv 27 行不自洽下钻")
    print("=" * 100)
    cols = ["ts_code", "trade_date", "close", "float_share", "circ_mv",
            "total_share", "total_mv", "turnover_rate", "pb", "is_st"]
    e = pd.read_parquet(E_PATH, columns=cols)
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    sub = e[etd == "20260828"].copy()
    close = pd.to_numeric(sub["close"], errors="coerce").to_numpy("float64")
    fsh = pd.to_numeric(sub["float_share"], errors="coerce").to_numpy("float64")
    cmv = pd.to_numeric(sub["circ_mv"], errors="coerce").to_numpy("float64")
    ok = np.isfinite(close) & np.isfinite(fsh) & (fsh > 0) & np.isfinite(cmv)
    calc = np.full(len(sub), np.nan)
    calc[ok] = close[ok] * fsh[ok]
    good = ok & np.isclose(calc, cmv, rtol=2e-3, atol=1.0)
    bad = ok & ~good
    print("  可检验=%d  自洽=%d  不自洽=%d" % (int(ok.sum()), int(good.sum()), int(bad.sum())))
    if bad.any():
        b = sub[bad].copy()
        b["_calc"] = calc[bad]
        b["_relerr"] = (b["_calc"] - pd.to_numeric(b["circ_mv"], errors="coerce")) / \
                       pd.to_numeric(b["circ_mv"], errors="coerce")
        b = b.sort_values("_relerr", key=lambda s: s.abs(), ascending=False)
        print("\n  不自洽行（按相对误差降序，全部列出）：")
        print("  %-10s %10s %14s %14s %14s %10s %8s"
              % ("ts_code", "close", "float_share", "circ_mv", "calc=close*fsh",
                 "rel_err", "is_st"))
        for _, r in b.iterrows():
            print("  %-10s %10.2f %14.1f %14.1f %14.1f %9.2f%% %8s"
                  % (r["ts_code"], r["close"], r["float_share"], r["circ_mv"],
                     r["_calc"], 100 * r["_relerr"], r["is_st"]))
        # 这些股票在相邻干净日是否也异常 → 判断是单日事件还是持续状态
        codes = set(b["ts_code"].astype(str))
        print("\n  追踪这 %d 只股票在 E 侧前后各日的 circ_mv 自洽性：" % len(codes))
        for dt in ["20260826", "20260827", "20260828", "20260831", "20260901",
                   "20260902", "20260911"]:
            s2 = e[(etd == dt) & (e["ts_code"].astype(str).isin(codes))]
            if not len(s2):
                print("    %s  该窗口无数据" % dt)
                continue
            c2 = pd.to_numeric(s2["close"], errors="coerce").to_numpy("float64")
            f2 = pd.to_numeric(s2["float_share"], errors="coerce").to_numpy("float64")
            m2 = pd.to_numeric(s2["circ_mv"], errors="coerce").to_numpy("float64")
            k2 = np.isfinite(c2) & np.isfinite(f2) & (f2 > 0) & np.isfinite(m2)
            if not k2.any():
                print("    %s  可检验=0" % dt)
                continue
            h2 = int(np.isclose(c2[k2] * f2[k2], m2[k2], rtol=2e-3, atol=1.0).sum())
            print("    %s  自洽 %d/%d = %.1f%%" % (dt, h2, int(k2.sum()),
                                                    100.0 * h2 / k2.sum()))
    del e, etd, sub
    gc.collect()


def find3_recent_baseline():
    print("\n" + "=" * 100)
    print("发现 3  NaN 密度改用『D 近期窗口』对『E-only 窗口』同期对比")
    print("=" * 100)
    print("  D 近期窗口 = %s ~ %s（E-only 起点前的连续区间）" % (RECENT_D_START, RECENT_D_END))
    print("  E-only 窗口 = %s ~ %s（9 个交易日）" % (EONLY_DATES[0], EONLY_DATES[-1]))
    cols = ["pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ratio", "dv_ttm",
            "is_st", "listed_days", "suspend_timing", "volume_ratio",
            "turnover_rate", "float_share", "up_limit"]
    d = pd.read_parquet(D_PATH, columns=cols)
    dtd = td8(pd.Series(d.index.get_level_values("trade_date"))).to_numpy()
    dsel = (dtd >= RECENT_D_START) & (dtd <= RECENT_D_END)
    dsub = d[dsel]
    d_n = int(dsel.sum())
    del d, dtd, dsel
    gc.collect()

    e = pd.read_parquet(E_PATH, columns=cols + ["trade_date", "ts_code"])
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    esel = np.isin(etd, EONLY_DATES)
    esub = e[esel]
    e_n = int(esel.sum())
    del e, etd, esel
    gc.collect()

    print("\n  D 近期窗口行数 = %d ；E-only 窗口行数 = %d" % (d_n, e_n))
    print("\n  %-18s %14s %14s %10s" % ("列", "D近期空值率", "E-only空值率", "差异pp"))
    for c in cols:
        a = nanrate(dsub[c])
        b = nanrate(esub[c])
        flag = "  <<<" if abs(a - b) > 5.0 else ""
        print("  %-18s %13.2f%% %13.2f%% %9.2f%s" % (c, a, b, b - a, flag))
    print("\n  判读：差异 < 5pp 视为同口径；> 5pp 需逐列定性（可能是新股/停牌/")
    print("        亏损股 PE 天然为空，不等于 E 有缺陷）。")
    del dsub, esub
    gc.collect()


def find4_near_dates_circmv():
    print("\n" + "=" * 100)
    print("发现 4  扩大扫描：E 侧近期逐日 circ_mv / total_mv 自洽率（找坏批次是否连续）")
    print("=" * 100)
    cols = ["trade_date", "close", "float_share", "circ_mv",
            "total_share", "total_mv"]
    e = pd.read_parquet(E_PATH, columns=cols)
    etd = td8(pd.Series(e["trade_date"])).to_numpy()
    dates = sorted(set(np.unique(etd).tolist()))
    tail = [d for d in dates if d >= "20260801"]
    print("  %-12s %8s %22s %22s" % ("日期", "行数", "circ_mv 自洽", "total_mv 自洽"))
    for dt in tail:
        m = etd == dt
        s = e[m]
        c = pd.to_numeric(s["close"], errors="coerce").to_numpy("float64")
        f = pd.to_numeric(s["float_share"], errors="coerce").to_numpy("float64")
        v = pd.to_numeric(s["circ_mv"], errors="coerce").to_numpy("float64")
        t = pd.to_numeric(s["total_share"], errors="coerce").to_numpy("float64")
        w = pd.to_numeric(s["total_mv"], errors="coerce").to_numpy("float64")
        k1 = np.isfinite(c) & np.isfinite(f) & (f > 0) & np.isfinite(v)
        h1 = int(np.isclose(c[k1] * f[k1], v[k1], rtol=2e-3, atol=1.0).sum()) if k1.any() else 0
        k2 = np.isfinite(c) & np.isfinite(t) & (t > 0) & np.isfinite(w)
        h2 = int(np.isclose(c[k2] * t[k2], w[k2], rtol=2e-3, atol=1.0).sum()) if k2.any() else 0
        f1 = "  <<<" if k1.any() and h1 < k1.sum() else ""
        f2 = "  <<<" if k2.any() and h2 < k2.sum() else ""
        print("  %-12s %8d %11d/%-6d %11d/%-6d%s%s"
              % (dt, int(m.sum()), h1, int(k1.sum()), h2, int(k2.sum()), f1, f2))
    del e, etd
    gc.collect()


def main():
    find1_missing_cols()
    find2_circmv_20260828()
    find3_recent_baseline()
    find4_near_dates_circmv()
    print("\nDRILL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
