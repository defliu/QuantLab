# -*- coding: utf-8 -*-
"""XVAL JOINT Q3：rank_ladder OOS 日均超额 0.1803%(DE) vs 0.183%(OP) 微差定位。

同一缓存/同一策略路径（笔数与回撤两侧一致），枚举「日均超额」的候选计算口径：
  (a) nanmean(dr - mkt)                —— DE 上轮 / 生产 audit_stats_walkforward 口径
  (b) nanmean(dr) - nanmean(mkt)       —— 均值分别求再相减（NaN 天数不对齐时 ≠ a）
  (c) mean(dr - mkt, mkt NaN 填 0)     —— 末位 NaN 当 0 处理
对照 OP 3 位显示值：abs IS +0.132/OOS +0.181/FULL +0.150；rank IS -0.043/OOS +0.183/FULL +0.054
DE 4 位值：      abs IS +0.1320/OOS +0.1811/FULL +0.1495；rkl IS -0.0429/OOS +0.1803
只读；复用 xval_de_bt_cache.parquet。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, PROJ)
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import data_config as DC
import xval_de_p345 as P   # 上轮脚本：load/make_per_day/simulate/mdd 常量一致

IS_CUT = pd.Timestamp("2025-07-01")


def main():
    df, mkt = P.load()
    dates = sorted(pd.Timestamp(d) for d in df["trade_date"].unique())
    mkt_by_date = dict(zip(mkt["trade_date"], mkt["mkt"]))
    assert len(mkt_by_date) == len(dates)

    print("[open_map/down_map 构建...]", flush=True)
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "down_limit"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["ts_code"].isin(set(df["ts_code"].unique()))]
    daily = daily[daily["trade_date"] >= P.START - pd.Timedelta(days=45)]
    daily = daily.set_index(["trade_date", "ts_code"])
    open_map = daily["open"].to_dict()
    down_map = daily["down_limit"].to_dict()

    segs = {
        "IS": [d for d in dates if d < IS_CUT],
        "OOS": [d for d in dates if d >= IS_CUT],
        "FULL": dates,
    }
    variants = {"abs": "total_abs", "rank_ladder": "total_rkl"}
    op_disp = {("abs", "IS"): 0.132, ("abs", "OOS"): 0.181, ("abs", "FULL"): 0.150,
               ("rank_ladder", "IS"): -0.043, ("rank_ladder", "OOS"): 0.183,
               ("rank_ladder", "FULL"): 0.054}

    print(f"\n{'变体':<12}{'段':<6}{'笔数':>5}{'回撤':>8}"
          f"{'(a)nanmean(diff)':>18}{'(b)均值差':>16}{'(c)NaN填0':>16}{'OP显示(3dp)':>14}")
    for vname, col in variants.items():
        per_day = P.make_per_day(df, col)
        for seg_name, seg in segs.items():
            tr, dr, skip, skipdn, delist = P.simulate(seg, per_day, 60.0, P.N_HOLD,
                                                      P.SLIP, open_map, down_map)
            mkt_seg = np.array([mkt_by_date[d] for d in seg])
            ex_a = float(np.nanmean(dr - mkt_seg))
            ex_b = float(np.nanmean(dr) - np.nanmean(mkt_seg))
            ex_c = float(np.mean(dr - np.nan_to_num(mkt_seg)))
            opv = op_disp[(vname, seg_name)]
            print(f"{vname:<12}{seg_name:<6}{len(tr):>5}{P.mdd(dr):>8.1%}"
                  f"{ex_a:>17.4%}{ex_b:>16.4%}{ex_c:>16.4%}{opv:>13.3%}")

    # 末位 NaN 天的影响分解（OOS ladder 为例）
    print("\n[末位 NaN 天分解] OOS 段 ladder：")
    per_day = P.make_per_day(df, "total_rkl")
    seg = segs["OOS"]
    tr, dr, *_ = P.simulate(seg, per_day, 60.0, P.N_HOLD, P.SLIP, open_map, down_map)
    mkt_seg = np.array([mkt_by_date[d] for d in seg])
    print(f"  OOS 天数={len(seg)}  末天={seg[-1].date()}  末天 mkt={mkt_seg[-1]!r}"
          f"  末天策略日收益={dr[-1]:+.6%}")
    print(f"  有效天数(a)={np.sum(~np.isnan(dr - mkt_seg))}  "
          f"dr 均值(274天)={np.nanmean(dr):+.6%}  dr 均值(273天)={np.nanmean(dr[:-1]):+.6%}")
    print(f"  (a)={np.nanmean(dr - mkt_seg):+.6%}  (b)={np.nanmean(dr) - np.nanmean(mkt_seg):+.6%}"
          f"  差={np.nanmean(dr) - np.nanmean(mkt_seg) - np.nanmean(dr - mkt_seg):+.6%}")
    print(f"  (b) 四舍五入 3 位 = {(np.nanmean(dr) - np.nanmean(mkt_seg)) * 100:.3f}%  （OP 显示 +0.183%）")
    print(f"  (a) 四舍五入 3 位 = {np.nanmean(dr - mkt_seg) * 100:.3f}%  （DE 报 +0.1803%）")


if __name__ == "__main__":
    main()
