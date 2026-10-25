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
    rets, mkt = {}, {}
    for N in NS:
        sd["opN"] = g.shift(-1 - N)
        m = sd.dropna(subset=["op1", "opN"])
        # 审计修复（2026-10-25）：停牌日 open=0 会被算成 -100%，必须先剔除
        m = m[(m["op1"] > 0) & (m["opN"] > 0)]
        m = m.assign(r=m["opN"] / m["op1"] - 1)
        m = m[np.isfinite(m["r"])]
        rets[N] = {c: dict(zip(sub["ds"], sub["r"])) for c, sub in m.groupby("ts_code")}
        mkt[N] = m.groupby("ds")["r"].mean().to_dict()   # 同信号日同持有期全市场等权基准
    cal = sorted(sd["ds"].unique().tolist())
    return rets, mkt, cal


def main():
    log("# G2 持有期 N15 vs N10 复核 · 2026-10-25（opencode，只读）")
    log("")
    rets, mkt, cal = build_rets()
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
            rec["m%d" % N] = mkt[N].get(d0, np.nan)
        if ok:
            rows.append(rec)
    m = pd.DataFrame(rows)
    sig_max = max(m["date"])
    log("> G2(live) 公共窗口：信号日 %s ~ %s，n=%d 笔 / %d 个信号日"
        % (m["date"].min(), sig_max, len(m), m["date"].nunique()))
    log("> ⚠️ 相邻信号日 + 同日多票高度相关，逐笔 t 会严重高估显著度；以「日级聚合 t」为准")
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
    # 日级聚合（审计新增）：每天先等权平均，消除同日多票相关性
    log("")
    log("### 日级聚合（每日先在票内等权平均，再检验日序列）")
    log("| 口径 | 日数 | 日均差(pp) | t | 过线 |")
    log("|---|---|---|---|---|")
    for name, thr in (("rank<=15", 15), ("rank<=3", 3)):
        sub = m[m["rank"].fillna(99) <= thr]
        dm = sub.groupby("date").apply(lambda x: (x["r15"] - x["r10"]).mean())
        t = t_of(dm)
        log("| %s | %d | %+.2f | %+.2f | %s |" % (
            name, len(dm), dm.mean() * 100, t, "*T2*" if abs(t) > 2 else "否"))
    # 市场调整（审计新增）：剔除大盘 beta，只看持有期本身的超额边际
    log("")
    log("### 市场调整后（减去同信号日同持有期全市场等权收益）")
    log("| 口径 | n | 超额差(pp) | t | 说明 |")
    log("|---|---|---|---|---|")
    for name, thr in (("rank<=15 逐笔", 15), ("rank<=3 逐笔", 3)):
        sub = m[m["rank"].fillna(99) <= thr].copy()
        sub["e"] = (sub["r15"] - sub["m15"]) - (sub["r10"] - sub["m10"])
        log("| %s | %d | %+.2f | %+.2f | |" % (name, len(sub), sub["e"].mean() * 100, t_of(sub["e"])))
    for name, thr in (("rank<=15 日级", 15), ("rank<=3 日级", 3)):
        sub = m[m["rank"].fillna(99) <= thr].copy()
        sub["e"] = (sub["r15"] - sub["m15"]) - (sub["r10"] - sub["m10"])
        de = sub.groupby("date")["e"].mean()
        log("| %s | %d | %+.2f | %+.2f | |" % (name, len(de), de.mean() * 100, t_of(de)))
    # 日期块 bootstrap（审计新增）
    rng = np.random.default_rng(20261025)
    dm = m.groupby("date").apply(lambda x: (x["r15"] - x["r10"]).mean())
    v = dm.to_numpy()
    bs = np.array([v[rng.integers(0, len(v), len(v))].mean() for _ in range(5000)])
    log("")
    log("日期块 bootstrap（重抽信号日 5000 次，rank<=15）：均值 %+.2fpp，95%%CI [%+.2f, %+.2f]，P(均值<0)=%.2f"
        % (v.mean() * 100, np.percentile(bs, 2.5) * 100, np.percentile(bs, 97.5) * 100, (bs < 0).mean()))
    # 逐月分段稳健性
    log("")
    log("### 逐月分段（rank<=15）")
    log("| 信号月 | n | 日数 | 毛差(pp/笔) | 毛差日级 t | 超额差(pp) | 超额日级 t |")
    log("|---|---|---|---|---|---|---|")
    m["month"] = m["date"].str[:7]
    m["e"] = (m["r15"] - m["m15"]) - (m["r10"] - m["m10"])
    for mo, sub in m.groupby("month"):
        d = sub["r15"] - sub["r10"]
        dm = sub.groupby("date").apply(lambda x: (x["r15"] - x["r10"]).mean())
        de = sub.groupby("date")["e"].mean()
        log("| %s | %d | %d | %+.2f | %s | %+.2f | %s |" % (
            mo, len(d), len(dm), d.mean() * 100,
            "%+.2f" % t_of(dm) if len(dm) >= 5 else "n<5",
            sub["e"].mean() * 100,
            "%+.2f" % t_of(de) if len(de) >= 5 else "n<5"))
    log("")
    log("### 审计更正（2026-10-25 复核）")
    log("- 上一版（同日首跑）口径 `n=198 / 差 -2.47pp / t=-3.08` 有两处缺陷：")
    log("  1. **数据缺陷**：300773.SZ（信号日 2026-09-11）在 2026-10-08~10-14 停牌，`open=0` 被算成")
    log("     `r15 = 0/51.57-1 = -100%`，单笔 diff=-95.5pp，把均值从 -2.00pp 放大到 -2.47pp（占 19%）。")
    log("  2. **统计缺陷**：198 笔仅 27 个信号日（日均 6.8 只），同日多票 + 相邻日 N=10 窗口重叠 →")
    log("     逐笔 t 严重高估。日级聚合后 t 不显著（见上表）。")
    log("- 结论：`t>2` 的「过线」在正确口径下**从未成立**；9 月的毛差主要来自市场 beta（超额差见逐月表）。")
    out = os.path.join(RESULTS, "G2持有期N15vsN10复核_20261025.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\n报告:", out)


if __name__ == "__main__":
    main()
