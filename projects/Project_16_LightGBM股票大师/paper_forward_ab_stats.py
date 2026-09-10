# -*- coding: utf-8 -*-
"""纸面三臂对比统计 v3（2026-09-10 修复，T-20260910-001）：
每臂按各自最佳持有期回填 N open→open 收益：
  G2(live)  = N15（live_trail 实盘口径最优 +0.213%）
  v3_enh    = N10
  融合      = N10（live_trail 实盘口径最优 +0.211%）

v3 修复（相对 v2）：
1. 到期判断改为「交易日历」口径：信号日 + hold + 1 个交易日 >= 最新交易日才算到期；
   v2 用自然日 hold*2+2 过保守（15 交易日实际约 21 自然日），最早信号被误判未到期。
2. 收益数据源改用 data_live/merged_daily_full.parquet（主库+增量合并，最新到当日收盘）；
   v2 用 D:/astock/daily/stock_daily.parquet 周更主库（最新仅 08-21），收益回填滞后。

用法：python paper_forward_ab_stats.py
"""
import os
import sys

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJ)
import data_config as DC

REAL = os.path.join(DC.DATA_DIR, "real")
MERGED = os.path.join(DC.LIVE_DIR, "merged_daily_full.parquet")  # 主库+增量合并（最新收盘）
ARMS = {  # 臂 -> (csv, 持有期N)
    "G2(live,N15)": (os.path.join(REAL, "paper_forward_live.csv"), 15),
    "v3_enh(N10)": (os.path.join(REAL, "paper_forward_ab_v3enh.csv"), 10),
    "融合(N10)": (os.path.join(REAL, "paper_forward_ens.csv"), 10),
}

_RET_CACHE = {}
_CAL = None


def _calendar():
    """交易日历：从 merged_daily_full 提取（比 ashare_trade_dates.txt 新，避免陈旧日历误判未到期）"""
    global _CAL
    if _CAL is None:
        sd = pd.read_parquet(MERGED).reset_index()
        sd["ds"] = pd.to_datetime(sd["trade_date"]).dt.strftime("%Y-%m-%d")
        _CAL = sorted(sd["ds"].unique().tolist())
        print(f"  交易日历: {len(_CAL)} 日 | 最新 {_CAL[-1]}")
    return _CAL


def load_open(hold):
    if hold in _RET_CACHE:
        return _RET_CACHE[hold]
    sd = pd.read_parquet(MERGED, columns=["open", "adj_factor"]).reset_index()
    sd["trade_date"] = pd.to_datetime(sd["trade_date"])
    sd["adj_open"] = sd["open"] * sd["adj_factor"]
    ao = sd.set_index(["ts_code", "trade_date"])["adj_open"].sort_index()
    op1 = ao.groupby(level=0).shift(-1)
    opN = ao.groupby(level=0).shift(-1 - hold)
    m = pd.DataFrame({"op1": op1, "opN": opN}).reset_index()
    m["ds"] = m["trade_date"].dt.strftime("%Y-%m-%d")
    out = {c: dict(zip(sub["ds"], sub["opN"] / sub["op1"] - 1)) for c, sub in m.groupby("ts_code")}
    _RET_CACHE[hold] = out
    return out


def main():
    today = pd.Timestamp.today()
    cal = _calendar()
    print(f"=== 纸面三臂对比（各自最优持有期，交易日到期口径，截至 {today.date()}）===")
    print(f"{'臂':<14}{'候选':>6}{'已到期':>6}{'收益均值':>10}{'胜率':>8}{'rank<=2笔':>9}")
    for name, (path, hold) in ARMS.items():
        if not os.path.exists(path):
            print(f"{name:<14} 无文件")
            continue
        df = pd.read_csv(path, encoding="utf-8-sig")
        ret_map = load_open(hold)
        rets = []
        for _, r in df.iterrows():
            d0 = str(r["date"])
            if d0 in cal:
                i = cal.index(d0)
                if i + hold + 1 >= len(cal):
                    continue  # 尚未到期（信号日 + hold + 1 交易日 > 最新）
            else:
                # 日历外兜底：自然日 hold*2+2 保守口径
                if (today - pd.Timestamp(d0)).days < hold * 2 + 2:
                    continue
            # ret_map[code][d0] = 信号日 d0 起持有 hold 天 open→open 收益（含复权）
            v = ret_map.get(str(r["code"]), {}).get(d0)
            if pd.notna(v):
                rets.append(float(v))
        mean = float(np.mean(rets)) if rets else np.nan
        win = float(np.mean([x > 0 for x in rets])) if rets else np.nan
        r2 = len(df[df["rank"] <= 2]) if "rank" in df.columns else len(df)
        print(f"{name:<14}{len(df):>6}{len(rets):>6}{mean:>10.4f}{win:>8.1%}{r2:>9}")


if __name__ == "__main__":
    main()
