# -*- coding: utf-8 -*-
"""XVAL JOINT Q1 追加：全阈值对照（OP: 60:1→4 / 58:3→11 / 55:11→19 / 52:20→39 / 50:32→53）
+ 池统计对齐 + 三只 58.5 票的 est/F6/北交所属性排查。只读。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import data_config as DC

CSV = os.path.join(HERE, "xval_de_p1_top100_20260928.csv")


def main():
    pre = pd.read_csv(CSV, dtype={"ts_code": str})
    pre["neu"] = pre["total_new"] - (pre["SC_F5"] - 5.0)

    print("[池统计对齐] OP 报 max=61.5 中位=47.5 min=36.0 / F5均值2.170 / ind均值-5.26 中位-5.95")
    print(f"  mine: total max={pre['total_new'].max()} 中位={pre['total_new'].median()} "
          f"min={pre['total_new'].min()} | F5均值={pre['SC_F5'].mean():.3f} | "
          f"ind均值={pre['industry_pct'].mean():.2f} 中位={pre['industry_pct'].median():.2f}")
    print(f"  prob max={pre['prob'].max():.4f} rank100={pre['prob'].min():.4f}（OP 报 [0.7042, 0.6406]）")

    print("\n[全阈值表] 红线 | 原始(DE) | 原始(OP) | 中性(DE) | 中性(OP)")
    op_orig = {60: 1, 58: 3, 55: 11, 52: 20, 50: 32}
    op_neu = {60: 4, 58: 11, 55: 19, 52: 39, 50: 53}
    for thr in (60, 58, 55, 52, 50):
        no = int((pre["total_new"] >= thr).sum())
        nn = int((pre["neu"] >= thr).sum())
        print(f"  {thr} | {no} | {op_orig[thr]} | {nn} | {op_neu[thr]}"
              f"{'   <- 差1' if no - op_orig[thr] == 1 and nn - op_neu[thr] == 1 else ''}")

    # 每档差集票
    print("\n[差集票]（DE 计入、按签名推断 OP 未计入的票）")
    for thr in (60, 58, 55, 52, 50):
        o_set = set(pre.loc[pre["total_new"] >= thr, "ts_code"])
        n_set = set(pre.loc[pre["neu"] >= thr, "ts_code"])
        print(f"  thr={thr}: 原始通过 {sorted(o_set)}")
        print(f"          中性通过 {sorted(n_set)}")

    # 北交所票
    bj = pre[pre["ts_code"].str.endswith(".BJ")]
    print(f"\n[BJ 票] 池内共 {len(bj)} 只：")
    print(bj[["ts_code", "prob", "total_new", "SC_F5", "SC_F6", "industry_pct"]].to_string(index=False))

    # 三只 58.5 票 + 600876 的 est 明细（daily_basic 0928 pe_ttm/turnover_rate）
    daily = pd.read_parquet(DC.DAILY_BASIC, columns=["pe_ttm", "turnover_rate"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["trade_date"] == pd.Timestamp("2026-09-28")].set_index("ts_code")
    print("\n[est 明细]（daily_basic 2026-09-28）")
    for c in ["000607.SZ", "000786.SZ", "920651.BJ", "001332.SZ", "600876.SH", "920163.BJ"]:
        if c in daily.index:
            r = daily.loc[c]
            print(f"  {c}: pe_ttm={r['pe_ttm']!r}  turnover_rate={r['turnover_rate']!r}")
        else:
            print(f"  {c}: 不在 daily_basic 0928（est reindex 必 NaN -> F6=5）")
        row = pre[pre["ts_code"] == c]
        if len(row):
            rr = row.iloc[0]
            print(f"        CSV: SC_F6={rr['SC_F6']:.0f} total={rr['total_new']} "
                  f"快照pe_ttm={rr['pe_ttm']!r} turn_ma5={rr['turn_ma5']!r}")


if __name__ == "__main__":
    main()
