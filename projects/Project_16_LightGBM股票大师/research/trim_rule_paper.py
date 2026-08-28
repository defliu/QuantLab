# coding: utf-8
"""观察模式（paper）风控规则：连续N日跑输大盘 且 主力持续净流出M日 → 减仓候选。

⚠️ 仅观察/记录，绝不卖出；不修改正式版本（qmt_monitor.py 不动）。
⚠️ 依据 research/results/trim_rule_backtest_report.md（2020-2026 回测）：该信号前向收益无
    显著预测力（证伪），本脚本只用于观察它在当前持仓上的触发情况，不代表建议执行减仓。
用法：python research/trim_rule_paper.py
数据：E:/astock daily + moneyflow（周更，可能滞后数日，见日志提示）
"""
import os
import sys
import time

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

A = r"E:\astock"
N_DEFAULT = 3
M_DEFAULT = 3


def streak(s):
    grp = (s != s.shift()).cumsum()
    return s.groupby(grp).cumsum().where(s, 0)


def current_holdings():
    """从成交记录 FIFO 推导当前持仓（与 reconcile_trades 一致）。"""
    import csv
    from collections import deque
    log = os.path.join(PROJ, "data", "qmt_trade_log.csv")
    q = {}
    if os.path.exists(log):
        with open(log, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                code, side = r.get("code", ""), r.get("side", "")
                try:
                    vol = int(float(r["vol"]))
                except (TypeError, ValueError, KeyError):
                    continue
                q.setdefault(code, 0)
                q[code] = q[code] + vol if side == "BUY" else q[code] - vol
    return {c: v for c, v in q.items() if v > 0}


def main():
    holdings = current_holdings()
    if not holdings:
        print("无持仓")
        return
    codes = list(holdings.keys())
    print(f"当前持仓: {holdings}")

    d = pd.read_parquet(os.path.join(A, "daily", "stock_daily.parquet"),
                        columns=["pct_chg", "close", "is_st"]).reset_index()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    d = d[d["ts_code"].isin(codes)].sort_values(["ts_code", "trade_date"])
    d["ret"] = d["pct_chg"] / 100.0
    mkt = d.groupby("trade_date")["ret"].median().rename("idx_ret")
    d = d.merge(mkt, on="trade_date", how="left")
    d["under"] = d["ret"] < d["idx_ret"]
    d["under_streak"] = d.groupby("ts_code")["under"].transform(streak)

    m = pd.read_parquet(os.path.join(A, "moneyflow", "moneyflow.parquet"),
                        columns=["buy_lg_amount", "buy_elg_amount", "sell_lg_amount", "sell_elg_amount"]).reset_index()
    m["trade_date"] = pd.to_datetime(m["trade_date"])
    m["main_net"] = m["buy_lg_amount"] + m["buy_elg_amount"] - m["sell_lg_amount"] - m["sell_elg_amount"]
    m = m[m["ts_code"].isin(codes)].sort_values(["ts_code", "trade_date"])
    m["out"] = m["main_net"] < 0
    m["out_streak"] = m.groupby("ts_code")["out"].transform(streak)

    last_d = d.groupby("ts_code").tail(1).set_index("ts_code")
    last_m = m.groupby("ts_code").tail(1).set_index("ts_code")
    mf_date = m["trade_date"].max().date()

    print(f"\n资金流数据最新日期: {mf_date}（周更，可能滞后；近几日主力净额可能未含）")
    print(f"{'代码':<11}{'持仓':>6}{'跑输天数':>8}{'净流出天数':>10}{'N=3&M=3触发':>12}")
    for code in codes:
        us = int(last_d.loc[code, "under_streak"]) if code in last_d.index else -1
        os_ = int(last_m.loc[code, "out_streak"]) if code in last_m.index else -1
        trig = "⚠️ 触发" if (us >= N_DEFAULT and os_ >= M_DEFAULT) else "否"
        print(f"{code:<11}{holdings[code]:>6}{us:>8}{os_:>10}{trig:>12}")
    print("\n[观察模式] 仅记录触发情况，未执行任何卖出；正式版本未改动。")
    print("回测结论见 research/results/trim_rule_backtest_report.md（该信号无显著预测力，勿据此减仓）")


if __name__ == "__main__":
    main()
