# coding: utf-8
"""选股相关性诊断 · 探针5（只读）。

排查探针4 暴露的异常：近60日相关系数最大值=1.0000。
需要确认是否为停牌/零收益序列导致的假高相关，以及
「最少有效样本数 + 零收益占比」这类前置过滤条件应设多少。
"""
import io
import os

import numpy as np
import pandas as pd

ASTOCK = "D:/astock"
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(os.environ.get("TEMP", "."), "probe5_report.txt")

_buf = []
_stdout_print = print


def print(*args, **kwargs):
    _buf.append(" ".join(str(a) for a in args))
    _stdout_print(*args, **kwargs)


def flush_report():
    with io.open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_buf))
    _stdout_print("REPORT=%s lines=%d" % (REPORT, len(_buf)))


def main():
    pd.set_option("display.width", 240)
    pd.set_option("display.max_rows", 200)

    sel_dir = os.path.join(PROJ, "data", "selections")
    codes = []
    for p in [os.path.join(sel_dir, "20260930_selection_full.csv"),
              os.path.join(sel_dir, "g2", "20260930_g2_top10.csv"),
              os.path.join(sel_dir, "g2_ens", "20260930_g2_top10.csv")]:
        if os.path.exists(p):
            codes.extend(pd.read_csv(p, encoding="utf-8-sig")["ts_code"].astype(str).tolist())
    codes = sorted(set(codes))

    md = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet"),
                         columns=["close", "pct_chg", "amount", "turnover_rate"])
    mdates = sorted(md.index.get_level_values(0).unique())
    win = [d for d in mdates if str(d)[:10] >= "2026-06-01"]
    sub = md.loc[(slice(win[0], win[-1]), codes), :]
    pc = sub["pct_chg"].unstack(level=1).tail(60) / 100.0

    print("=" * 74)
    print("[1] 每票收益样本质量（近60日）")
    rows = []
    for c in pc.columns:
        s = pc[c].dropna()
        zero = int((s.abs() < 1e-9).sum())
        rows.append((c, len(s), zero, round(zero / max(len(s), 1), 4),
                     round(float(s.std()), 6) if len(s) > 1 else 0.0))
    q = pd.DataFrame(rows, columns=["ts_code", "n", "zero_n", "zero_ratio", "std"])
    print(q.sort_values("zero_ratio", ascending=False).to_string(index=False))

    print("")
    print("=" * 74)
    print("[2] 相关矩阵=1.0 的配对明细")
    cm = pc.corr()
    pairs = []
    cl = list(cm.columns)
    for i in range(len(cl)):
        for j in range(i + 1, len(cl)):
            v = cm.iloc[i, j]
            if pd.notna(v) and v > 0.90:
                pairs.append((cl[i], cl[j], round(float(v), 4),
                              int(pc[cl[i]].notna().sum()), int(pc[cl[j]].notna().sum())))
    pairs.sort(key=lambda x: -x[2])
    print("相关系数>0.90 的配对数=%d" % len(pairs))
    for p in pairs[:25]:
        print("  %s %s corr=%.4f n_a=%d n_b=%d" % p)

    print("")
    print("=" * 74)
    print("[3] 过滤后（零收益占比<50% 且 std>0）重算")
    keep = [c for c in pc.columns
            if pc[c].notna().sum() >= 30
            and (pc[c].abs() < 1e-9).sum() / max(pc[c].notna().sum(), 1) < 0.5
            and float(pc[c].std() or 0) > 1e-9]
    print("保留=%d 剔除=%s" % (len(keep), [c for c in pc.columns if c not in keep]))
    if len(keep) >= 2:
        cm2 = pc[keep].corr()
        v = cm2.values.copy()
        np.fill_diagonal(v, np.nan)
        off = v[~np.isnan(v)]
        print("过滤后 均值=%.4f 最大=%.4f (>0.7配对=%d)"
              % (float(np.nanmean(v)), float(np.nanmax(v)),
                 int((off > 0.7).sum() // 2)))
        hi = []
        for i in range(len(keep)):
            for j in range(i + 1, len(keep)):
                if pd.notna(v[i, j]) and v[i, j] > 0.7:
                    hi.append((keep[i], keep[j], round(float(v[i, j]), 4)))
        for x in hi[:20]:
            print("  高相关 %s %s %.4f" % x)

    print("")
    print("=" * 74)
    print("[4] 窗口敏感性：40/60/90 日（用 pct_chg）")
    for w in (40, 60, 90):
        sub2 = sub["pct_chg"].unstack(level=1).tail(w) / 100.0
        cc = sub2[[c for c in keep]] if len(keep) >= 2 else sub2
        cmx = cc.corr()
        arr = cmx.values.copy()
        np.fill_diagonal(arr, np.nan)
        off = arr[~np.isnan(arr)]
        print("  win=%d n=%d 均值=%.4f 最大=%.4f >0.7对数=%d"
              % (w, len(cc.columns), float(np.nanmean(arr)), float(np.nanmax(arr)),
                 int((off > 0.7).sum() // 2)))

    print("")
    print("[PROBE5-DONE]")
    flush_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
