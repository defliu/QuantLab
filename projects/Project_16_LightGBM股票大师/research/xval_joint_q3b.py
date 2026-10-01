# -*- coding: utf-8 -*-
"""XVAL JOINT Q3b：枚举 rank-F5 阶梯构造变体，寻找能同时复现 OP 六个数字
（IS 48笔/-14.2%/-0.043 | OOS 55笔/-20.1%/+0.183 | FULL 103笔/-23.9%/+0.054）的构造。
全部从缓存派生（total = total_abs + (F5新 - F5abs)*1.0），不重算 516 日。只读。
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
import xval_de_p345 as P
import xval_de_scorecard as SCV

IS_CUT = pd.Timestamp("2025-07-01")


def build_total(df, f5_new):
    f5_abs = SCV.score_f5_vec(df["industry_pct"].values)
    return df["total_abs"].values + (f5_new - f5_abs) * 1.0


def main():
    df, mkt = P.load()
    dates = sorted(pd.Timestamp(d) for d in df["trade_date"].unique())
    mkt_by_date = dict(zip(mkt["trade_date"], mkt["mkt"]))

    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["open", "down_limit"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily["ts_code"] = daily["ts_code"].astype(str)
    daily = daily[daily["ts_code"].isin(set(df["ts_code"].unique()))]
    daily = daily[daily["trade_date"] >= P.START - pd.Timedelta(days=45)]
    daily = daily.set_index(["trade_date", "ts_code"])
    open_map = daily["open"].to_dict()
    down_map = daily["down_limit"].to_dict()

    r = df["ind_rank_pct"].values
    nan_r = np.isnan(r)

    # V1（上轮 DE 主口径）：>= 边界，NaN→5
    v1 = np.where(nan_r, 5.0, np.select([r >= 0.8, r >= 0.6, r >= 0.4, r >= 0.2],
                                        [10.0, 8.0, 5.0, 4.0], default=2.0))
    # V2：严格 > 边界，NaN→5
    v2 = np.where(nan_r, 5.0, np.select([r > 0.8, r > 0.6, r > 0.4, r > 0.2],
                                        [10.0, 8.0, 5.0, 4.0], default=2.0))
    # V3：>= 边界，NaN→2（最低档）
    v3 = np.where(nan_r, 2.0, np.select([r >= 0.8, r >= 0.6, r >= 0.4, r >= 0.2],
                                        [10.0, 8.0, 5.0, 4.0], default=2.0))
    # V4：>= 边界，NaN→10
    v4 = np.where(nan_r, 10.0, np.select([r >= 0.8, r >= 0.6, r >= 0.4, r >= 0.2],
                                         [10.0, 8.0, 5.0, 4.0], default=2.0))
    # V5：等距阶梯 {2,4,6,8,10}，>= 边界，NaN→5
    v5 = np.where(nan_r, 5.0, np.select([r >= 0.8, r >= 0.6, r >= 0.4, r >= 0.2],
                                        [10.0, 8.0, 6.0, 4.0], default=2.0))
    # V6：qcut 风格左开右闭 (0.8,1]→10 ... [0,0.2]→2（=V2 但 0 也归最低档，相同）
    # V7：np.floor(r*5) 映射 {2,4,5,8,10}
    v7 = np.where(nan_r, 5.0, np.array([2.0, 4.0, 5.0, 8.0, 10.0])[
        np.clip(np.floor(np.nan_to_num(r, nan=0.0) * 5).astype(int), 0, 4)])

    variants = {"V1_>=_nan5_(上轮DE)": v1, "V2_>_nan5": v2, "V3_>=_nan2": v3,
                "V4_>=_nan10": v4, "V5_等距246810": v5, "V7_floor": v7}

    segs = {"IS": [d for d in dates if d < IS_CUT],
            "OOS": [d for d in dates if d >= IS_CUT],
            "FULL": dates}
    print("目标(OP)：IS 48笔/-14.2%/-0.043 | OOS 55笔/-20.1%/+0.183 | FULL 103笔/-23.9%/+0.054")
    print("基线(DE V1)：IS 48笔/-14.2%/-0.0429 | OOS 55笔/-20.1%/+0.1803 | FULL 103笔/-23.9%/+0.0522")
    for name, f5 in variants.items():
        df2 = df.copy()
        df2["total_v"] = build_total(df, f5)
        line = [f"{name:<22}"]
        hit = True
        for seg_name, seg in segs.items():
            per_day = P.make_per_day(df2, "total_v")
            tr, dr, *_ = P.simulate(seg, per_day, 60.0, P.N_HOLD, P.SLIP, open_map, down_map)
            mkt_seg = np.array([mkt_by_date[d] for d in seg])
            ex = float(np.nanmean(dr - mkt_seg))
            line.append(f"{seg_name} {len(tr)}笔/{P.mdd(dr):.1%}/{ex:+.4%}")
        print(" | ".join(line))


if __name__ == "__main__":
    main()
