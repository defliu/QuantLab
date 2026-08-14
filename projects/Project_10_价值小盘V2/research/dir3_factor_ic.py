# coding=utf-8
"""方向3 (P1): 因子 IC 监控 —— 调仓日计算 BP 因子 IC/ICIR

在每个调仓日, 计算 BP (行业中性 z-score) 与下期收益的截面相关 (IC),
统计 IC 均值 / ICIR / 胜率, 输出 results/factor_ic_log.csv。

用法: python research/dir3_factor_ic.py
"""
import sys, os, time
sys.path.insert(0, r"D:\QuantLab\projects\Project_01_多因子IC小盘Alpha")
sys.path.insert(0, r"D:\QuantLab")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
import runner as R

RES = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results"))
os.makedirs(RES, exist_ok=True)

panel, ti, trade_dates = R.panel, R.ti, R.trade_dates
rebal = R.rebal
CFG = R.CFG

log = []
def p(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    log.append(s)


def compute_ic():
    """每个调仓日: BP因子 vs 下期 (次月 open -> 再下月 open) 收益"""
    records = []
    for i in range(len(rebal) - 1):
        d = pd.Timestamp(rebal[i])
        dd_data = panel.loc[d]
        cand = R.get_candidates(d, dd_data)
        if len(cand) < 10:
            continue
        score = R.scorer.score(d, cand, dd_data["pb"])
        if score is None or len(score) == 0:
            continue
        # 下期收益: rebal[i]次日 open -> rebal[i+1]次日 open
        e1_idx = ti[d] + 1
        if e1_idx >= len(trade_dates):
            continue
        e2_idx = ti[pd.Timestamp(rebal[i + 1])] + 1
        if e2_idx >= len(trade_dates):
            continue
        d1 = trade_dates[e1_idx]
        d2 = trade_dates[e2_idx]
        r1 = panel.loc[d1, "open"]
        r2 = panel.loc[d2, "open"]
        rets = {}
        for code in score.index:
            a, b = r1.get(code), r2.get(code)
            if a is not None and b is not None and a > 0 and b > 0:
                rets[code] = b / a - 1.0
        if len(rets) < 10:
            continue
        f = score.reindex(list(rets.keys()))
        r = pd.Series(rets)
        ic = f.corr(r)
        ic_rank = f.rank().corr(r.rank())
        records.append({"date": d.strftime("%Y-%m-%d"), "ic": ic, "ic_rank": ic_rank,
                        "n": len(rets), "next_period_ret": r.mean()})
    df = pd.DataFrame(records)
    return df


if __name__ == "__main__":
    p("计算调仓日 BP 因子 IC...")
    df = compute_ic()
    p("期数:", len(df))
    if len(df) == 0:
        p("无数据")
        sys.exit(0)
    ic_mean = df["ic"].mean()
    ic_std = df["ic"].std()
    icir = ic_mean / (ic_std + 1e-9) * np.sqrt(6)  # 双月调仓 => 年化 sqrt(6)
    win = (df["ic"] > 0).mean()
    p("IC 均值 = %.4f" % ic_mean)
    p("ICIR(年化) = %.3f" % icir)
    p("IC 胜率 = %.1f%%" % (win * 100))
    p("BP 因子与下期收益相关 (n=%d)" % len(df))

    p("\n逐年 IC:")
    df["year"] = pd.to_datetime(df["date"]).dt.year
    for y, g in df.groupby("year"):
        p("  %d: IC=%.4f  ICIR=%.2f  n=%d" % (y, g["ic"].mean(),
                                             g["ic"].mean() / (g["ic"].std() + 1e-9), len(g)))

    out_csv = os.path.join(RES, "factor_ic_log.csv")
    df.drop(columns=["year"], errors="ignore").to_csv(out_csv, index=False, encoding="utf-8-sig")
    p("已写入:", out_csv)
