# coding: utf-8
"""研究回测：风控规则「连续 N 日跑输大盘 且 主力持续净流出 M 日 → 减仓」是否有效。

不触碰正式版本；仅研究/回测验证。核心问题：
  1) 该信号触发后，个股前向收益（fwd5/10/20）是否显著跑输基线（未触发）？
  2) 若跑输，触发后减仓能避免多少损失？（信号组 - 基线组 前向收益差）
  3) 效果是否稳定（分年 / 样本外）？是否只对"近期强势（更可能被持有）"的股票有效？

口径：
  - 大盘 = 全市场个股当日收益率中位数（等权市场代理，与模型 rel_mom_20 同口径）
  - 主力净额 = buy_lg + buy_elg - sell_lg - sell_elg（E:/astock/moneyflow，周更）
  - 前向收益 = 收盘(ffill 停牌) 未来 k 个交易日收益, 与市场同期相减得超额
  - 宇宙 = is_st==0 且 上市>=120 交易日
"""
import json
import os
import sys
import time

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
RES = os.path.join(PROJ, "research", "results")
os.makedirs(RES, exist_ok=True)

A = r"E:\astock"
START = "2020-01-01"
END = "2026-08-21"  # moneyflow 最后日
N_LIST = [2, 3, 5]
M_LIST = [2, 3, 5]
FWDS = [5, 10, 20]


def load_daily():
    d = pd.read_parquet(os.path.join(A, "daily", "stock_daily.parquet"),
                        columns=["pct_chg", "close", "is_st", "listed_days"]).reset_index()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    d = d[(d["trade_date"] >= START) & (d["trade_date"] <= END)]
    d = d[(d["is_st"] == 0) & (d["listed_days"] >= 120)]
    d = d[["ts_code", "trade_date", "pct_chg", "close"]].copy()
    d["ret"] = d["pct_chg"] / 100.0
    return d


def load_moneyflow():
    m = pd.read_parquet(os.path.join(A, "moneyflow", "moneyflow.parquet"),
                        columns=["buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"]).reset_index()
    m["trade_date"] = pd.to_datetime(m["trade_date"])
    m = m[(m["trade_date"] >= START) & (m["trade_date"] <= END)]
    m["main_net"] = m["buy_lg_amount"] + m["buy_elg_amount"] - m["sell_lg_amount"] - m["sell_elg_amount"]
    return m[["ts_code", "trade_date", "main_net"]]


def streak(s):
    """连续 True 计数（False 归 0）。s 为按日期排序的 bool Series。"""
    grp = (s != s.shift()).cumsum()
    return s.groupby(grp).cumsum().where(s, 0)


def main():
    t0 = time.time()
    print("加载日线 ...", flush=True)
    d = load_daily()
    print(f"  日线 {len(d):,} 行", flush=True)
    print("加载资金流 ...", flush=True)
    m = load_moneyflow()
    print(f"  资金流 {len(m):,} 行", flush=True)

    df = d.merge(m, on=["ts_code", "trade_date"], how="left")
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    # 大盘 = 全市场当日收益中位数（等权市场代理，与模型 rel_mom_20 同口径；无外部依赖）
    mkt = df.groupby("trade_date")["ret"].median().rename("idx_ret")
    df = df.merge(mkt, on="trade_date", how="left")

    # 个股 vs 大盘
    df["under"] = df["ret"] < df["idx_ret"]
    df["out"] = df["main_net"] < 0
    df["under_streak"] = df.groupby("ts_code")["under"].transform(streak)
    df["out_streak"] = df.groupby("ts_code")["out"].transform(streak)

    # 前向收益（停牌 ffill 平值）
    df["close_f"] = df.groupby("ts_code")["close"].ffill()
    g = df.groupby("ts_code")["close_f"]
    for k in FWDS:
        df[f"fwd_{k}"] = g.shift(-k) / df["close_f"] - 1.0
    mkt_close = df.groupby("trade_date")["close_f"].median()
    for k in FWDS:
        df[f"mkt_fwd_{k}"] = df["trade_date"].map(mkt_close.shift(-k)) / df["trade_date"].map(mkt_close) - 1.0
        df[f"ex_{k}"] = df[f"fwd_{k}"] - df[f"mkt_fwd_{k}"]

    # 近期强势子集（近似"会被持有"的票：20日动量>0）
    df["mom20"] = df.groupby("ts_code")["close"].pct_change(20)
    df["strong"] = df["mom20"] > 0

    print(f"  合并后 {len(df):,} 行, 用时 {time.time()-t0:.0f}s", flush=True)

    report = []
    report.append(f"# 风控规则回测：连续N日跑输大盘 + 主力持续净流出M日 → 减仓")
    report.append("")
    report.append(f"> 生成时间 {time.strftime('%Y-%m-%d %H:%M:%S')} ｜ 区间 {START} ~ {END} ｜ 大盘=全市场收益中位数")
    report.append(f"> 口径：主力净额=买大单+买超大-卖大单-卖超大（E:/astock/moneyflow 周更）；前向=收盘(停牌ffill)未来k交易日；宇宙=非ST且上市≥120交易日")
    report.append(f"> 宇宙行数 {len(df):,}；仅研究回测，不构成投资建议、未接入正式版本")
    report.append("")

    for subset, label in [("all", "全宇宙"), ("strong", "仅近期强势(mom20>0，近似持仓)")]:
        sub = df if subset == "all" else df[df["strong"]].copy()
        report.append(f"## 一、{label}")
        report.append("")
        for N in N_LIST:
            for M in M_LIST:
                sig = (sub["under_streak"] >= N) & (sub["out_streak"] >= M)
                # 只统计连续触发的"起点日"，避免同一段信号被重复计数
                prev_sig = sig.groupby(sub["ts_code"]).shift(1).fillna(False)
                epi = sig & (~prev_sig)
                rows = sub[epi]
                if len(rows) < 30:
                    continue
                base = sub[~sig]
                line = [f"### N={N}, M={M} ｜ 触发样本 {len(rows):,}（全样本 {len(sub):,}）"]
                line.append("")
                line.append("| 指标 | 信号组(触发后) | 基线(未触发) | 差(信号-基线) |")
                line.append("|---|---|---|---|")
                for k in FWDS:
                    s_f = rows[f"fwd_{k}"].dropna()
                    b_f = base[f"fwd_{k}"].dropna()
                    s_ex = rows[f"ex_{k}"].dropna()
                    b_ex = base[f"ex_{k}"].dropna()
                    line.append(f"| fwd{k} 均值% | {s_f.mean()*100:+.2f} | {b_f.mean()*100:+.2f} | {(s_f.mean()-b_f.mean())*100:+.2f} |")
                    line.append(f"| fwd{k} 超额% | {s_ex.mean()*100:+.2f} | {b_ex.mean()*100:+.2f} | {(s_ex.mean()-b_ex.mean())*100:+.2f} |")
                    line.append(f"| fwd{k} 中位% | {s_f.median()*100:+.2f} | {b_f.median()*100:+.2f} | |")
                    line.append(f"| fwd{k} 下跌概率 | {100*(s_f<0).mean():.1f}% | {100*(b_f<0).mean():.1f}% | |")
                line.append("")
                report.extend(line)
    report.append("## 二、分年稳定性（N=3,M=3，全宇宙）")
    report.append("")
    sig3 = df["under_streak"] >= 3
    sig3 = sig3 & (df["out_streak"] >= 3)
    df["sig33"] = sig3
    yr = df[df["trade_date"].dt.year >= 2020].copy()
    yr["year"] = yr["trade_date"].dt.year
    report.append("| 年 | 触发数 | 信号fwd10均值% | 基线fwd10均值% | 差pp | 信号超额fwd10% |")
    report.append("|---|---|---|---|---|---|")
    for y, gsub in yr.groupby("year"):
        s = gsub[gsub["sig33"]]
        b = gsub[~gsub["sig33"]]
        if len(s) < 30:
            continue
        sm = s["fwd_10"].dropna().mean() * 100
        bm = b["fwd_10"].dropna().mean() * 100
        sex = s["ex_10"].dropna().mean() * 100
        report.append(f"| {y} | {len(s):,} | {sm:+.2f} | {bm:+.2f} | {sm-bm:+.2f} | {sex:+.2f} |")
    report.append("")

    txt = "\n".join(report)
    out = os.path.join(RES, "trim_rule_backtest_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print(txt)
    print(f"\n报告已写入: {out}")


if __name__ == "__main__":
    main()
