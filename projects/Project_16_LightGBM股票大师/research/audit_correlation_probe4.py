# coding: utf-8
"""选股相关性诊断 · 探针4（只读）。

只回答正式诊断器落地前剩下的决策问题：
  1. 修正 glob 后的 index_weight 两族真实文件数；
  2. 候选风格基准指数（有限集合）是否真的有成分数据、最新截面日期、成分数；
  3. pct_chg 字段对候选票的覆盖率（收益序列口径二选一）；
  4. 申万一级映射对候选票的命中率。
"""
import glob
import io
import os

import numpy as np
import pandas as pd

ASTOCK = "D:/astock"
IW = os.path.join(ASTOCK, "index_weight")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT = os.path.join(os.environ.get("TEMP", "."), "probe4_report.txt")

_buf = []
_stdout_print = print


def print(*args, **kwargs):
    _buf.append(" ".join(str(a) for a in args))
    _stdout_print(*args, **kwargs)


def flush_report():
    with io.open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(_buf))
    _stdout_print("REPORT=%s lines=%d" % (REPORT, len(_buf)))


def split_families():
    allf = sorted(glob.glob(os.path.join(IW, "index_weight_*.parquet")))
    main = [p for p in allf if "_data_" not in os.path.basename(p)]
    data = [p for p in allf if "_data_" in os.path.basename(p)]
    return main, data


def find_index_files(codes):
    """为每个目标 index_code 找出其成分文件（读首行 index_code 判定）。"""
    hits = {c: [] for c in codes}
    for p in sorted(glob.glob(os.path.join(IW, "index_weight_*.parquet"))):
        try:
            d = pd.read_parquet(p, columns=["index_code"])
        except Exception:
            continue
        if d.empty:
            continue
        code = str(d["index_code"].iloc[0])
        if code in hits:
            hits[code].append(p)
    return hits


def main():
    pd.set_option("display.width", 240)
    pd.set_option("display.max_rows", 200)

    print("=" * 74)
    print("[1] 修正 glob 后的文件族计数")
    main_f, data_f = split_families()
    print("index_weight(非data)=%d" % len(main_f))
    print("index_weight_data=%d" % len(data_f))
    print("合计=%d" % (len(main_f) + len(data_f)))
    print("样例 main=%s" % [os.path.basename(p) for p in main_f[:3]])
    print("样例 data=%s" % [os.path.basename(p) for p in data_f[:3]])

    print("")
    print("=" * 74)
    print("[2] 候选风格/宽基基准指数的成分可得性")
    ib = pd.read_parquet(os.path.join(IW, "index_basic.parquet"))
    ib["ts_code"] = ib["ts_code"].astype(str)
    name_by_code = dict(zip(ib["ts_code"], ib["name"].astype(str)))

    style_codes = ["000918.CSI", "000919.CSI", "399630.SZ", "399604.SZ", "399371.SZ",
                   "000922.CSI", "000923.CSI", "399370.SZ", "399372.SZ"]
    broad_codes = ["000300.SH", "000905.SH", "000852.SH", "000016.SH",
                   "399006.SZ", "000688.SH", "932000.CSI"]
    all_codes = style_codes + broad_codes

    hits = find_index_files(all_codes)
    rows = []
    for code in all_codes:
        paths = hits.get(code) or []
        best_date = ""
        best_n = 0
        for p in paths:
            try:
                d = pd.read_parquet(p, columns=["trade_date"])
            except Exception:
                continue
            if d.empty:
                continue
            md = str(d["trade_date"].max())[:8]
            if md > best_date:
                best_date = md
                best_n = int((d["trade_date"].astype(str) == str(d["trade_date"].max())).sum())
        rows.append((code, name_by_code.get(code, "<无名>"), len(paths),
                     best_date, best_n))
    out = pd.DataFrame(rows, columns=["index_code", "name", "files", "max_date", "last_n"])
    print(out.to_string(index=False))
    print("")
    print("有成分的风格基准=%d/%d" % (int((out.iloc[:len(style_codes)]["files"] > 0).sum()),
                                     len(style_codes)))

    print("")
    print("=" * 74)
    print("[3] 候选集合 + pct_chg 覆盖")
    sel_dir = os.path.join(PROJ, "data", "selections")
    paths3 = {
        "v13": os.path.join(sel_dir, "20260930_selection_full.csv"),
        "g2": os.path.join(sel_dir, "g2", "20260930_g2_top10.csv"),
        "ens": os.path.join(sel_dir, "g2_ens", "20260930_g2_top10.csv"),
    }
    codes = []
    for tag, p in paths3.items():
        if not os.path.exists(p):
            print("缺失 %s -> %s" % (tag, p))
            continue
        d = pd.read_csv(p, encoding="utf-8-sig")
        print("%s shape=%s cols=%s" % (tag, d.shape, list(d.columns)))
        codes.extend(d["ts_code"].astype(str).tolist())
    codes = sorted(set(codes))
    print("并集候选(%d)=%s" % (len(codes), codes))

    md = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet"),
                         columns=["close", "pct_chg", "amount", "turnover_rate"])
    mdates = sorted(md.index.get_level_values(0).unique())
    last_md = str(mdates[-1])[:10]
    print("主行情末日=%s 总天数=%d" % (last_md, len(mdates)))

    win = [d for d in mdates if str(d)[:10] >= "2026-06-01"]
    sub = md.loc[(slice(win[0], win[-1]), codes), :]
    piv_px = sub["close"].unstack(level=1)
    piv_pc = sub["pct_chg"].unstack(level=1)
    print("窗口 %s ~ %s 交易日=%d" % (str(win[0])[:10], str(win[-1])[:10], len(win)))
    print("close 覆盖: min=%d max=%d" % (int(piv_px.count().min()), int(piv_px.count().max())))
    print("pct_chg 覆盖: min=%d max=%d" % (int(piv_pc.count().min()), int(piv_pc.count().max())))
    rets_px = piv_px.pct_change()
    rets_pc = piv_pc / 100.0
    both = int(np.isfinite(rets_px.values).sum())
    print("pct_change 有效样本=%d  pct_chg 有效样本=%d" % (both, int(np.isfinite(rets_pc.values).sum())))
    a = rets_px.tail(60).corr().values
    b = rets_pc.tail(60).corr().values
    m = np.isfinite(a) & np.isfinite(b)
    print("近60日相关矩阵: 均值 pct_chg=%.4f close=%.4f | 最大 pct_chg=%.4f close=%.4f"
          % (float(np.nanmean(b)), float(np.nanmean(a)),
             float(np.nanmax(b)), float(np.nanmax(a))))
    d1 = a[m] - b[m]
    print("两口径逐元素最大绝对差=%.6f 均值绝对差=%.6f"
          % (float(np.max(np.abs(d1))), float(np.mean(np.abs(d1)))))
    # 单位一致性检查：pct_chg/100 与 close.pct_change() 是否同量纲
    r1 = np.nanmean(np.abs(rets_px.values[np.isfinite(rets_px.values)]))
    r2 = np.nanmean(np.abs(rets_pc.values[np.isfinite(rets_pc.values)]))
    print("平均绝对收益 close.pct_change=%.6f  pct_chg/100=%.6f" % (r1, r2))

    print("")
    print("=" * 74)
    print("[4] 申万一级映射命中率")
    sw = pd.read_parquet(os.path.join(ASTOCK, "index", "sw_l1_daily.parquet"))
    sd = sorted(sw.index.get_level_values(0).unique())
    sub2 = sw.loc[sd[-1]].reset_index()
    ok = sub2["con_codes"].map(lambda x: isinstance(x, (list, tuple, np.ndarray)) and len(x) > 0)
    valid = sub2[ok]
    print("最新截面=%s 有效行业=%d" % (str(sd[-1])[:10], len(valid)))
    print("valid cols=%s" % list(valid.columns))
    mp = {}
    for _, r in valid.iterrows():
        nm = str(r["name"])
        for c in r["con_codes"]:
            mp[str(c)] = nm
    hit = [c for c in codes if c in mp]
    print("候选命中申万一级 %d/%d" % (len(hit), len(codes)))
    for c in codes:
        print("  %s -> %s" % (c, mp.get(c, "<未覆盖>")))

    print("")
    print("[PROBE4-DONE]")
    flush_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
