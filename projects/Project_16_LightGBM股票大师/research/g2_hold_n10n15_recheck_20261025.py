# -*- coding: utf-8 -*-
"""G2 持有期 N15 vs N10 复核（2026-10-25，opencode，只读）。

背景：2026-10-08《P16三策略优化方案》B1 建议 G2 N15→N10 回退，证据 N15−N10 −1.4~−2.0pp
但 t≈−1.5 未过 t>2 显著线，且公共窗口仅 08-17~09-08 单月。方案门槛 =「公共窗口累积过
t>2（约 10 月中下旬）」。本脚本用最新数据重跑同口径配对（复用
paper_hold_exit_diag_20261008.py 的 P1 方法），输出报告
results/G2持有期N15vsN10复核_20261025.md。
"""
import math
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
REAL = os.path.join(PROJ, "data", "real")
RESULTS = os.path.join(PROJ, "results")
MERGED = os.path.join(PROJ, "data_live", "merged_daily_full.parquet")

NS = [10, 15]
LIVE = os.path.join(REAL, "paper_forward_live.csv")

OUT = []


def log(s=""):
    print(s)
    OUT.append(s)


def t_of(d):
    d = pd.Series(d).dropna()
    if len(d) < 5 or d.std(ddof=1) == 0:
        return float("nan")
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def build_rets():
    sd = pd.read_parquet(MERGED, columns=["open", "adj_factor"]).reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd["ds"] = sd["trade_date"].dt.strftime("%Y-%m-%d")
    sd["adj_open"] = sd["open"] * sd["adj_factor"]
    sd = sd.sort_values(["ts_code", "trade_date"])
    g = sd.groupby("ts_code")["adj_open"]
    sd["op1"] = g.shift(-1)
    rets = {}
    for N in NS:
        sd["opN"] = g.shift(-1 - N)
        m = sd.dropna(subset=["op1", "opN"])
        rets[N] = {c: dict(zip(sub["ds"], sub["opN"] / sub["op1"] - 1))
                   for c, sub in m.groupby("ts_code")}
    cal = sorted(sd["ds"].unique().tolist())
    return rets, cal


def main():
    log("# G2 持有期 N15 vs N10 复核 · 2026-10-25（opencode，只读）")
    log("")
    rets, cal = build_rets()
    log("> 数据源 merged_daily_full.parquet，最新交易日 %s" % cal[-1])
    df = pd.read_csv(LIVE, encoding="utf-8-sig")
    df = df.drop_duplicates(subset=["date", "code"], keep="last")
    nmax = max(NS)
    rows = []
    for _, r in df.iterrows():
        d0 = str(r["date"])
        if d0 not in cal:
            continue
        i = cal.index(d0)
        if i + nmax + 1 >= len(cal):
            continue
        rec = {"date": d0, "code": r["code"], "rank": r.get("rank", np.nan)}
        ok = True
        for N in NS:
            v = rets[N].get(str(r["code"]), {}).get(d0)
            if pd.isna(v):
                ok = False
                break
            rec["r%d" % N] = float(v)
        if ok:
            rows.append(rec)
    m = pd.DataFrame(rows)
    sig_max = max(m["date"])
    log("> G2(live) 公共窗口：信号日 %s ~ %s，n=%d 笔（10-08 时仅 n=91）"
        % (m["date"].min(), sig_max, len(m)))
    log("")
    log("| 口径 | n | N10 均值 | N15 均值 |")
    log("|---|---|---|---|")
    for name, thr in (("rank<=15", 15), ("rank<=3", 3)):
        sub = m[m["rank"].fillna(99) <= thr]
        log("| %s | %d | %+.2f%% | %+.2f%% |" % (
            name, len(sub),
            sub["r10"].mean() * 100, sub["r15"].mean() * 100))
    log("")
    log("| 配对 N15-N10 | n | 差(pp/笔) | t | 过线 |")
    log("|---|---|---|---|---|")
    for name, thr in (("rank<=15", 15), ("rank<=3", 3)):
        sub = m[m["rank"].fillna(99) <= thr]
        d = sub["r15"] - sub["r10"]
        t = t_of(d)
        log("| %s | %d | %+.2f | %+.2f | %s |" % (
            name, len(d), d.mean() * 100, t,
            "*T2*" if abs(t) > 2 else "否"))
    # 逐月分段稳健性
    log("")
    log("### 逐月分段（rank<=15，N15-N10 配对差）")
    log("| 信号月 | n | 差(pp/笔) | t |")
    log("|---|---|---|---|")
    m["month"] = m["date"].str[:7]
    for mo, sub in m.groupby("month"):
        d = sub["r15"] - sub["r10"]
        log("| %s | %d | %+.2f | %s |" % (
            mo, len(d), d.mean() * 100,
            "%+.2f" % t_of(d) if len(d) >= 5 else "n<5"))
    out = os.path.join(RESULTS, "G2持有期N15vsN10复核_20261025.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\n报告:", out)


if __name__ == "__main__":
    main()
