# -*- coding: utf-8 -*-
"""追盈(trailing)语义分析 analyze_trail_semantics.py —— 只读，验证「高点回撤8%」语义 bug 的影响。

问题（2026-09-07 用户提出，601579 实锤）：
1) 当天买入(T+1锁定)的票也会触发追盈信号——卖不掉，纯噪音；
2) 追盈 peak 只需 > 成本(任意微盈) 就开始追踪，回撤 8% 触发时实际可能是亏损卖出，
   「追盈」变成「追跌」；用户期望是「先涨够 X% 再从峰值回撤」。
本脚本对同一批入场（红线60/N=10 top2 可执行）逐笔模拟 5 种追盈语义，对比每笔收益/触发诊断：
  current          = 现引擎/实盘语义（peak>cost 即追踪）
  act8             = 激活阈值 +8%（peak≥cost×1.08 才追踪）
  act8_floor       = act8 + 保本底线 line=max(cost, peak×0.92)
  act5_floor       = 激活 +5% + 保本底线
  act8_floor_nb    = act8_floor + 入场日不并入峰值（T+1 卫生）
输出：每语义的 单笔均值/胜率/TRAIL次数/TRAIL亏损笔数/TRAIL平均收益/均持有。
"""
import os
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
sys.path.insert(0, PROJ)
os.chdir(PROJ)
os.environ["BT_SLIPS"] = "0.001"

import scan_rotate_cost_real as BT  # noqa: E402
import paper_forward_exit as PFE  # noqa: E402  (复用 load_ohlc)

VARIANTS = {
    "current": {"act": None, "floor": False, "no_buyday": False},
    "act8": {"act": 0.08, "floor": False, "no_buyday": False},
    "act8_floor": {"act": 0.08, "floor": True, "no_buyday": False},
    "act5_floor": {"act": 0.05, "floor": True, "no_buyday": False},
    "act8_floor_nb": {"act": 0.08, "floor": True, "no_buyday": True},
}
N = 10
THR = 60.0


def simulate_trail(sdf, en, v):
    """单笔入场模拟追盈（含止损/止盈/期满），语义由 v 控制。返回 dict 或 None。"""
    o = sdf["open"].values.astype(float)
    h = sdf["high"].values.astype(float)
    down = sdf["down_limit"].values.astype(float)
    if len(o) == 0 or not (np.isfinite(o[en]) and o[en] > 0):
        return None
    entry = float(o[en])
    buy_i = en - 1
    peak = None
    missing = 0
    act = v["act"]
    floor = v["floor"]
    no_buyday = v["no_buyday"]
    for j in range(en, len(o)):
        if not (np.isfinite(o[j]) and o[j] > 0):
            missing += 1
            if missing >= 60:
                return {"ret": -0.5, "reason": "DELIST", "exit_j": j}
            continue
        missing = 0
        local_peak = peak if (peak and peak > 0) else entry
        if not (no_buyday and j == en):
            if np.isfinite(h[j]) and h[j] > 0:
                peak = max(local_peak, float(h[j]))
        ret = o[j] / entry - 1
        if np.isfinite(down[j]) and o[j] <= down[j] + 1e-9:
            continue
        if j - buy_i < 2:
            continue
        if j - buy_i >= N + 1:
            return {"ret": ret, "reason": "MATURE", "exit_j": j}
        if ret <= -0.07:
            return {"ret": ret, "reason": "STOP", "exit_j": j}
        if ret >= 0.15:
            return {"ret": ret, "reason": "TP", "exit_j": j}
        trail_on = local_peak > entry
        if act is not None:
            trail_on = local_peak >= entry * (1 + act)
        if trail_on:
            line = local_peak * (1 - 0.08)
            if floor:
                line = max(entry, line)
            if o[j] <= line:
                return {"ret": ret, "reason": "TRAIL", "exit_j": j}
    return None


def main():
    print("[1/2] build_per_day（红线60/N=10 入场集）...")
    dates, per_day, market_daily, open_map = BT.build_per_day()
    df, trades, main_last, inc_last = PFE.load_ohlc()
    tset = pd.Index(trades)
    entries = []
    for i, d in enumerate(dates):
        row = per_day[d]
        cand = row[row["total_new"] >= THR]
        if len(cand) == 0:
            continue
        cand = cand[cand.apply(BT._executable, axis=1)]
        if len(cand) == 0:
            continue
        for code, s in cand.nlargest(2, "total_new").iterrows():
            en = tset.searchsorted(pd.Timestamp(d), side="right")
            if en >= len(trades):
                continue
            entries.append({"date": d, "code": code, "en": en})
    print("    入场集 %d 笔（%s ~ %s）" % (len(entries), dates[0].date(), dates[-1].date()))

    print("[2/2] 逐笔模拟 5 种追盈语义 ...")
    cols = {"date": [], "code": [], "en": []}
    for v in VARIANTS:
        cols[v + "_ret"] = []
        cols[v + "_reason"] = []
        cols[v + "_hold"] = []
    for e in entries:
        code = e["code"]
        sdf = df.xs(code, level="ts_code").reindex(trades)
        cols["date"].append(e["date"])
        cols["code"].append(code)
        cols["en"].append(e["en"])
        for v in VARIANTS:
            r = simulate_trail(sdf, e["en"], VARIANTS[v])
            if r is None:
                cols[v + "_ret"].append(np.nan)
                cols[v + "_reason"].append("PEND")
                cols[v + "_hold"].append(np.nan)
            else:
                cols[v + "_ret"].append(r["ret"])
                cols[v + "_reason"].append(r["reason"])
                cols[v + "_hold"].append(r["exit_j"] - e["en"])
    res = pd.DataFrame(cols)

    print("\n=== 各语义对比（同 %d 笔入场）===" % len(res))
    hdr = "%-16s %6s %8s %6s %8s %8s %8s %8s" % (
        "语义", "n", "均值%", "胜率%", "TRAIL", "TRAIL亏", "TRAIL均值%", "均持有")
    print(hdr)
    print("-" * len(hdr))
    for v in VARIANTS:
        sub = res.dropna(subset=[v + "_ret"])
        if len(sub) == 0:
            print("%-16s 无到期" % v)
            continue
        tr = sub[sub[v + "_reason"] == "TRAIL"]
        print("%-16s %6d %8.3f %6.1f %8d %8d %8.3f %8.1f" % (
            v, len(sub), sub[v + "_ret"].mean() * 100, (sub[v + "_ret"] > 0).mean() * 100,
            len(tr), int((tr[v + "_ret"] < 0).sum()) if len(tr) else 0,
            tr[v + "_ret"].mean() * 100 if len(tr) else float("nan"),
            sub[v + "_hold"].mean()))
    # 出场原因构成
    print("\n=== 出场原因构成 ===")
    for v in VARIANTS:
        sub = res.dropna(subset=[v + "_ret"])
        print("  %-16s %s" % (v, sub[v + "_reason"].value_counts().to_dict()))

    # 保存
    out = os.path.join(PROJ, "data", "real", "trail_semantics_analysis.csv")
    res.to_csv(out, index=False, encoding="utf-8-sig")
    print("\n明细:", out)


if __name__ == "__main__":
    main()
