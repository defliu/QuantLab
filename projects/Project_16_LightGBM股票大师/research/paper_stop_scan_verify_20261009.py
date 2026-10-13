# -*- coding: utf-8 -*-
"""P16 止损档位扫描独立复核（2026-10-09，opencode，只读）。

对照《讨论室/P16三策略止损方向审计_20261009.md》第三节，独立实现 simulate
（不复用 paper_hold_exit_diag_20261008 的变体函数），在同 55 笔样本上复核：
  (1) 固定止损档位扫描 -5/-7/-10/-12/-15 vs 无止损（stop-only 口径）：均值/胜率/止损率/配对 t
  (2) 实盘三元组口径（-7% + TP15 + TRAIL8，开盘触发）逐数字对表
  (3) 子期拆分（前半 08-17~08-31 / 后半 09-01~09-14）
  (4) 止损后反弹（锁定 vs 拿到期，17 笔）

用法：python research/paper_stop_scan_verify_20261009.py
输出：控制台 + results/P16止损档位扫描复核_20261009.md
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

N = 10
TP, TRAIL_PCT, ACT = 0.15, 0.08, 0.08
DELIST_DAYS, DELIST_LOSS = 60, 0.5
SCAN = [0.05, 0.07, 0.10, 0.12, 0.15]  # 固定止损档位（正数表示 -X%）

OUT = []


def log(s=""):
    print(s)
    OUT.append(s)


def t_of(d):
    d = pd.Series(d).dropna()
    if len(d) < 5 or d.std(ddof=1) == 0:
        return float("nan")
    return float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def sim(sub, en, stop=None, tp=None, trail=None):
    """独立实现：T+1 开盘入场，T+1 一字跌停禁卖，j-buy_i>=N+1 开盘到期卖。
    stop = 止损线（正数，如 0.07 表示 -7%），开盘价触发（与审计 ① 同口径）。
    tp/trail = None 时关闭。"""
    o = sub["open"].values.astype(float)
    h = sub["high"].values.astype(float)
    down = sub["down_limit"].values.astype(float)
    if len(o) == 0 or not (np.isfinite(o[en]) and o[en] > 0):
        return None
    entry = float(o[en])
    line = entry * (1 - stop) if stop else None
    buy_i = en - 1
    peak = None
    missing = 0
    for j in range(en, len(o)):
        if not (np.isfinite(o[j]) and o[j] > 0):
            missing += 1
            if missing >= DELIST_DAYS:
                return {"ret": -DELIST_LOSS, "reason": "DELIST", "j": j}
            continue
        missing = 0
        local_peak = peak if (peak and peak > 0) else entry
        if j > en and np.isfinite(h[j]) and h[j] > 0:
            peak = max(local_peak, float(h[j]))
        if np.isfinite(down[j]) and o[j] <= down[j] + 1e-9:
            continue
        if j - buy_i < 2:
            continue
        if j - buy_i >= N + 1:
            return {"ret": o[j] / entry - 1, "reason": "MATURE", "j": j}
        sell = fill = None
        if stop is not None and o[j] <= line + 1e-9:
            sell, fill = "STOP", o[j]
        elif tp is not None and o[j] / entry - 1 >= tp:
            sell, fill = "TP", o[j]
        elif trail is not None and local_peak >= entry * (1 + ACT):
            tl = max(entry, local_peak * (1 - trail))
            if o[j] <= tl:
                sell, fill = "TRAIL", o[j]
        if sell:
            return {"ret": fill / entry - 1, "reason": sell, "j": j}
    return None


def main():
    log("# P16 止损档位扫描独立复核（对照 止损方向审计_20261009）")
    log("")
    import paper_forward_exit as pfe
    df, trades, main_last, inc_last = pfe.load_ohlc()
    tset = pd.Index(trades)
    live = pd.read_csv(pfe.LIVE_LOG, encoding="utf-8-sig")
    live = live.drop_duplicates(subset=["date", "code"], keep="last")
    sub = live[live["rank"] <= 3].sort_values(["date", "rank"]).reset_index(drop=True)

    recs = []
    for _, r in sub.iterrows():
        d = pd.Timestamp(r["date"])
        pos = tset.searchsorted(d, side="right")
        if pos >= len(trades):
            continue
        code = r["code"]
        try:
            sdf = df.xs(code, level="ts_code").reindex(trades)
        except KeyError:
            continue
        if not (np.isfinite(sdf["open"].values.astype(float)[pos])
                and sdf["open"].values.astype(float)[pos] > 0):
            continue
        rec = {"date": str(d)[:10], "code": code}
        rec["none"] = sim(sdf, pos)
        for sp in SCAN:
            rec["s%d" % int(sp * 100)] = sim(sdf, pos, stop=sp)
        rec["full"] = sim(sdf, pos, stop=0.07, tp=TP, trail=TRAIL_PCT)
        out = {"date": rec["date"], "code": code}
        for k, v in rec.items():
            if k in ("date", "code"):
                continue
            out[k + "_ret"] = v["ret"] if v else np.nan
            out[k + "_reason"] = v["reason"] if v else ""
        recs.append(out)
    m = pd.DataFrame(recs)
    cols = ["none"] + ["s%d" % int(x * 100) for x in SCAN] + ["full"]
    mature = m.dropna(subset=[c + "_ret" for c in cols])
    log("> 数据合并最新 %s；候选 %d 笔，全变体到期可判定 %d 笔" % (
        str(trades[-1])[:10], len(m), len(mature)))
    log("")
    log("### (1)+(2) 档位扫描（stop-only）+ 实盘三元组（开盘触发，配对基准=无止损）")
    log("")
    log("| 口径 | 止损率 | 均值 | 胜率 | 差 vs 无止损(pp) | 配对 t |")
    log("|---|---|---|---|---|---|")
    n0 = mature["none_ret"]
    log("| 无止损 | - | %+.2f%% | %.1f%% | - | - |" % (n0.mean() * 100, (n0 > 0).mean() * 100))
    for sp in SCAN:
        k = "s%d" % int(sp * 100)
        s = mature[k + "_ret"]
        ns = int((mature[k + "_reason"] == "STOP").sum())
        d = s - n0
        log("| 固定 -%d%% | %d%% | %+.2f%% | %.1f%% | %+.2f | %+.2f%s |" % (
            int(sp * 100), round(100.0 * ns / len(s)), s.mean() * 100,
            (s > 0).mean() * 100, d.mean() * 100, t_of(d),
            " *T2*" if abs(t_of(d)) > 2 else ""))
    s = mature["full_ret"]
    ns = int((mature["full_reason"] == "STOP").sum())
    d = s - n0
    dist = mature["full_reason"].value_counts()
    log("| 实盘三元组(-7/+15/8追盈) | %d%% | %+.3f%% | %.1f%% | %+.3f | %+.2f%s |" % (
        round(100.0 * ns / len(s)), s.mean() * 100, (s > 0).mean() * 100,
        d.mean() * 100, t_of(d), " *T2*" if abs(t_of(d)) > 2 else ""))
    log("> 实盘三元组出场分布: %s" % " / ".join(
        "%s %d" % (k, int(v)) for k, v in dist.items()))

    log("")
    log("### (3) 子期拆分（none vs 实盘三元组 / stop-only -7%）")
    log("")
    mature2 = mature.copy()
    mature2["half"] = np.where(mature2["date"] <= "2026-08-31", "前半 08-17~08-31", "后半 09-01~09-14")
    log("| 子期 | n | none | 三元组 | 差 | t | stop-only -7% | 差 | t |")
    log("|---|---|---|---|---|---|---|---|---|")
    for half, g in mature2.groupby("half"):
        d1 = g["full_ret"] - g["none_ret"]
        d2 = g["s7_ret"] - g["none_ret"]
        log("| %s | %d | %+.2f%% | %+.2f%% | %+.2f | %+.2f | %+.2f%% | %+.2f | %+.2f%s |" % (
            half, len(g), g["none_ret"].mean() * 100, g["full_ret"].mean() * 100,
            d1.mean() * 100, t_of(d1), g["s7_ret"].mean() * 100,
            d2.mean() * 100, t_of(d2), " *T2*" if abs(t_of(d2)) > 2 else ""))

    log("")
    log("### (4) 止损后反弹（实盘三元组 STOP 的笔，n=%d）" % ns)
    stops = mature[mature["full_reason"] == "STOP"]
    locked = stops["full_ret"]
    mat = stops["none_ret"]
    rebound = (1 + mat) / (1 + locked) - 1
    log("- 止损锁定均值 %+.2f%% / 拿到期均值 %+.2f%% → 单笔差 %+.2fpp（t=%+.2f）" % (
        locked.mean() * 100, mat.mean() * 100, (mat - locked).mean() * 100,
        t_of(mat - locked)))
    log("- 从止损价到期末的反弹：均值 %+.2f%% / 中位 %+.2f%% / 上涨占比 %.0f%%" % (
        rebound.mean() * 100, replay_median(rebound) * 100, 100.0 * (rebound > 0).mean()))

    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, "P16止损档位扫描复核_20261009.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(OUT) + "\n")
    print("\n报告:", out)


def replay_median(s):
    return float(pd.Series(s).median())


if __name__ == "__main__":
    main()
