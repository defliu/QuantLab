# -*- coding: utf-8 -*-
"""纸面三臂对比统计（2026-09-09 上线）：
臂1 G2(live) = data/real/paper_forward_live.csv
臂2 v3_enh  = data/real/paper_forward_ab_v3enh.csv
臂3 融合    = data/real/paper_forward_ens.csv
对每臂候选回填 N=10 open→open 收益（已过期部分），输出均值/胜率/笔数对比。
用法：python paper_forward_ab_stats.py [--hold 10]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

REAL = os.path.join(DC.DATA_DIR, "real")
ASTOCK = r"D:/astock"
ARMS = {
    "G2(live)": os.path.join(REAL, "paper_forward_live.csv"),
    "v3_enh": os.path.join(REAL, "paper_forward_ab_v3enh.csv"),
    "融合": os.path.join(REAL, "paper_forward_ens.csv"),
}


def load_open():
    sd = pd.read_parquet(os.path.join(ASTOCK, "daily", "stock_daily.parquet")).reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd["adj_open"] = sd["open"] * sd["adj_factor"]
    ao = sd.set_index(["ts_code", "trade_date"])["adj_open"].sort_index()
    op1 = ao.groupby(level=0).shift(-1)
    opN = ao.groupby(level=0).shift(-1 - 10)
    m = pd.DataFrame({"op1": op1, "opN": opN}).reset_index()
    m["ds"] = m["trade_date"].dt.strftime("%Y-%m-%d")
    return {c: dict(zip(sub["ds"], sub["opN"] / sub["op1"] - 1)) for c, sub in m.groupby("ts_code")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=10)
    args = ap.parse_args()
    ret_map = load_open()
    today = pd.Timestamp.today()
    print(f"=== 纸面三臂对比（N={args.hold} open→open，截至 {today.date()}）===")
    print(f"{'臂':<12}{'候选数':>6}{'已过期':>6}{'收益均值':>10}{'胜率':>8}{'最高rank<=2笔':>12}")
    for name, path in ARMS.items():
        if not os.path.exists(path):
            print(f"{name:<12} 无文件")
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        if "date" not in df.columns:
            print(f"{name:<12} 列缺失")
            continue
        rets = []
        for _, r in df.iterrows():
            d0 = pd.Timestamp(r["date"])
            if (today - d0).days < 18:  # 信号日+11开盘卖出 ≈ 15 自然日，加余量
                continue
            rm = ret_map.get(str(r["code"]), {})
            v = rm.get(r["date"])
            if pd.notna(v):
                rets.append(float(v))
        n_all = len(df)
        n_done = len(rets)
        mean = float(np.mean(rets)) if rets else np.nan
        win = float(np.mean([x > 0 for x in rets])) if rets else np.nan
        r2 = df[df["rank"] <= 2] if "rank" in df.columns else df
        print(f"{name:<12}{n_all:>6}{n_done:>6}{mean:>10.4f}{win:>8.1%}{len(r2):>12}")


if __name__ == "__main__":
    main()
