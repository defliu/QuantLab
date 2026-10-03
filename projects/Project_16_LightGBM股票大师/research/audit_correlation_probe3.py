# coding: utf-8
"""选股相关性诊断 · 探针3（只读）。

目标：
  1. 风格/成长/价值类指数在 index_weight 家族中的成分覆盖（成分数、最新日期）；
  2. 宽基成分文件的实际结构（列、日期格式、单日截面大小）；
  3. 候选股票历史收益序列构造可行性（从 stock_daily MultiIndex 抽取）；
  4. 20260930 三套候选实际内容 + 与 20261008 副本的差异。
"""
import glob
import io
import os
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

ASTOCK = "D:/astock"
IW = os.path.join(ASTOCK, "index_weight")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(os.environ.get("TEMP", "."), "probe3_report.txt")

_buf = []
_stdout_print = print


def print(*args, **kwargs):
    _buf.append(" ".join(str(a) for a in args))
    _stdout_print(*args, **kwargs)


def flush_report():
    with io.open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_buf))
    _stdout_print("REPORT=%s lines=%d" % (REPORT, len(_buf)))


def collect_index_files():
    out = {}
    for p in sorted(glob.glob(os.path.join(IW, "index_weight_*.parquet"))):
        # glob 的 * 会吞掉 _data_ 子族，必须按文件名排除，否则两族重复计数
        tag = "index_weight_data" if "_data_" in os.path.basename(p) else "index_weight"
        out.setdefault(tag, []).append(p)
    return out


def main():
    pd.set_option("display.width", 240)
    pd.set_option("display.max_rows", 300)

    print("=" * 74)
    print("[1] 风格/成长/价值/主题指数的成分覆盖（index_weight 家族）")
    ib = pd.read_parquet(os.path.join(IW, "index_basic.parquet"))
    fam = collect_index_files()
    print("index_weight=%d  index_weight_data=%d"
          % (len(fam["index_weight"]), len(fam["index_weight_data"])))

    # 逐文件读 index_code + trade_date，建立 index_code -> (max_date, rows, families)
    stat = defaultdict(lambda: {"max_date": "", "rows": 0, "fams": set()})
    for tag, paths in fam.items():
        for p in paths:
            try:
                d = pd.read_parquet(p, columns=["index_code", "trade_date"])
            except Exception:
                continue
            if d.empty:
                continue
            code = str(d["index_code"].iloc[0])
            s = stat[code]
            md = str(d["trade_date"].max())[:8]
            if md > s["max_date"]:
                s["max_date"] = md
            s["rows"] += len(d)
            s["fams"].add(tag)
    print("两族合计唯一 index_code=%d" % len(stat))

    cat_map = dict(zip(ib["ts_code"].astype(str), ib["name"].astype(str)))
    rows = []
    for code, s in stat.items():
        cat = None
        m = ib[ib["ts_code"].astype(str) == code]
        if len(m):
            cat = m["category"].iloc[0]
        rows.append((code, cat_map.get(code, ""), cat, s["max_date"], s["rows"],
                     len(s["fams"])))
    allidx = pd.DataFrame(rows, columns=["index_code", "name", "category",
                                         "max_date", "rows", "fams"])
    print("")
    print("--- 风格类指数（category 含 成长/价值/风格/策略）成分数分布 ---")
    sty = allidx[allidx["category"].astype(str).str.contains("成长|价值|风格|策略", na=False)]
    print("风格类 index_code 数=%d" % len(sty))
    print(sty["rows"].describe().to_string())
    print("成分数>=20 的风格指数数=%d" % int((sty["rows"] >= 20).sum()))
    print("样例（成分数最大 20 个）:")
    print(sty.sort_values("rows", ascending=False).head(20).to_string())

    print("")
    print("--- 有成分的风格指数里，是否存在知名风格宽基 ---")
    for kw in ["300成长", "300价值", "巨潮", "中证1000成长", "中证1000价值",
               "国证成长", "国证价值", "上证180", "中证800"]:
        hit = allidx[allidx["name"].astype(str).str.contains(kw, na=False, regex=False)]
        if len(hit):
            print("  %s -> %s" % (kw, hit[["index_code", "name", "category",
                                            "max_date", "rows"]].head(4)
                                  .to_dict("records")))

    print("")
    print("=" * 74)
    print("[2] 宽基成分文件实际结构")
    for code in ["000300.SH", "000905.SH", "000852.SH", "000016.SH",
                 "399006.SZ", "000688.SH"]:
        paths = []
        for tag, ps in fam.items():
            for p in ps:
                try:
                    d0 = pd.read_parquet(p, columns=["index_code"])
                except Exception:
                    continue
                if d0.empty:
                    continue
                if str(d0["index_code"].iloc[0]) == code:
                    paths.append((tag, p))
                    break
        print("--- %s (%s) 文件数=%d ---" % (code, cat_map.get(code, ""), len(paths)))
        for tag, p in paths:
            d = pd.read_parquet(p)
            print("  [%s] %s shape=%s cols=%s" % (tag, os.path.basename(p),
                                                  d.shape, list(d.columns)))
            print("      trade_date dtype=%s min=%s max=%s 唯一天数=%d"
                  % (d["trade_date"].dtype, d["trade_date"].min(),
                     d["trade_date"].max(), d["trade_date"].nunique()))
            last = sorted(d["trade_date"].unique())[-1]
            sub = d[d["trade_date"] == last]
            print("      最新截面(%s) 成分数=%d con_code样例=%s"
                  % (last, len(sub), sub["con_code"].head(3).tolist()))
            print("      weight 统计: %s"
                  % (sub["weight"].describe()[["min", "50%", "max"]].to_dict()
                     if "weight" in sub.columns else "无 weight 列"))

    print("")
    print("=" * 74)
    print("[3] 候选历史收益序列构造可行性")
    sel_dir = os.path.join(PROJ, "data", "selections")
    g2_csv = os.path.join(sel_dir, "g2", "20260930_g2_top10.csv")
    ens_csv = os.path.join(sel_dir, "g2_ens", "20260930_g2_top10.csv")
    v13_csv = os.path.join(sel_dir, "20260930_selection_full.csv")
    future = {
        "g2": os.path.join(sel_dir, "g2", "20261008_g2_top10.csv"),
        "ens": os.path.join(sel_dir, "g2_ens", "20261008_g2_top10.csv"),
    }
    codes = []
    for p in [g2_csv, ens_csv, v13_csv]:
        if not os.path.exists(p):
            print("缺失: %s" % p)
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        print("--- %s shape=%s cols=%s" % (os.path.basename(p), d.shape, list(d.columns)))
        print(d.to_string())
        codes.extend(d["ts_code"].astype(str).tolist())
    codes = sorted(set(codes))
    print("")
    print("并集候选 ts_code (%d): %s" % (len(codes), codes))

    for tag, p in future.items():
        if not os.path.exists(p):
            print("未来副本不存在: %s" % p)
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        print("--- 未来副本 %s shape=%s trade_date=%s ---"
              % (os.path.basename(p), d.shape, d["trade_date"].unique().tolist()))
        base = {"g2": g2_csv, "ens": ens_csv}.get(tag)
        if base and os.path.exists(base):
            b = pd.read_csv(base, encoding="utf-8-sig")
            same = b["ts_code"].astype(str).tolist() == d["ts_code"].astype(str).tolist()
            print("    与 20260930 候选 ts_code 顺序完全相同=%s" % same)
            cmp_cols = [c for c in b.columns if c in d.columns and c != "trade_date"]
            diff = {c: int((b[c].astype(str) != d[c].astype(str)).sum())
                    for c in cmp_cols}
            print("    非 trade_date 列差异计数=%s" % diff)

    print("")
    print("--- 从 stock_daily 抽取候选收益序列（2026-06-01 起）---")
    md = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet"),
                         columns=["close", "pct_chg"])
    dates = sorted(md.index.get_level_values(0).unique())
    sel_dates = [d for d in dates if str(d) >= "2026-06-01"]
    print("可用交易日(%d): %s ... %s" % (len(sel_dates), str(sel_dates[0])[:10],
                                        str(sel_dates[-1])[:10]))
    avail = {}
    for c in codes:
        try:
            s = md.loc[(slice(sel_dates[0], sel_dates[-1]), c), "close"]
        except Exception as exc:
            avail[c] = "ERR %s" % exc
            continue
        avail[c] = len(s)
    print("候选覆盖天数: %s" % avail)
    # 抽取一次
    sub = md.loc[(slice(sel_dates[0], sel_dates[-1]), codes), :]
    print("抽取矩阵 shape=%s" % (sub.shape,))
    if len(sub) and len(codes) > 1:
        piv = sub["close"].unstack(level=1)
        print("pivot shape=%s" % (piv.shape,))
        rets = piv.pct_change()
        print("收益矩阵 shape=%s 有效样本=%d" % (rets.shape, int(rets.notna().sum().sum())))
        cm = rets.tail(60).corr()
        np.fill_diagonal(cm.values, np.nan)
        print("近60日相关矩阵均值=%.4f 最大=%.4f"
              % (float(np.nanmean(cm.values)), float(np.nanmax(cm.values))))

    print("")
    print("[PROBE3-DONE]")
    flush_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
