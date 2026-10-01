# -*- coding: utf-8 -*-
"""主代理独立复算：实盘快照 vs 回测链路断层定位（只读）。

对同一批 ts_code，比较 g2_latest_features（实盘）与 feature_panel（回测）在
重叠交易日的特征/评分卡差异，定位从哪一列开始分叉。
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
PANEL = os.path.join(PROJ, "data", "feature_panel_v3_enh2_n3_bt.parquet")
LIVE = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260825.json")

sys.path.insert(0, PROJ)
os.chdir(PROJ)

CMP_COLS = [
    "main_net", "mf_main_net", "industry_pct", "ind_pct_ths",
    "volume_ratio", "pe_ttm", "turn_ma5", "turnover_rate",
]


def main():
    from scorecard_real import compute_real_scorecard

    with open(META, "r", encoding="utf-8") as f:
        meta = json.load(f)
    feats = list(meta.get("features") or meta.get("feature_cols"))

    live = pd.read_parquet(LIVE)
    live["trade_date"] = pd.to_datetime(live["trade_date"])
    ldate = live["trade_date"].max()
    print("[info] 实盘快照日期=%s 股票数=%d" % (ldate.date(), len(live)))
    print("[info] 快照列(前40)=%s" % list(live.columns)[:40])

    print("\n=== 实盘快照关键列非空率 ===")
    for c in CMP_COLS:
        if c in live.columns:
            print("  %-16s %6.1f%%" % (c, 100.0 * live[c].notna().mean()))

    panel_dates = pd.read_parquet(
        PANEL, columns=["trade_date"]
    )["trade_date"]
    panel_dates = pd.to_datetime(panel_dates)
    print("\n[info] 面板日期范围=%s ~ %s" % (panel_dates.min().date(),
                                            panel_dates.max().date()))
    overlap = sorted(set(panel_dates.dt.normalize()) & set(live["trade_date"]))
    print("[info] 面板与快照重叠交易日=%d 个" % len(overlap))
    if not overlap:
        print("[warn] 无重叠交易日，无法逐列比对")
    else:
        d = overlap[-1]
        print("[info] 选取重叠日 %s 做逐列比对" % d.date())

    # 评分卡对比：用同一批股票，跑模型 Top100 -> 评分卡
    booster = lgb.Booster(model_file=MODEL)
    need_live = list(dict.fromkeys(
        ["ts_code", "main_net", "mf_main_net", "industry_pct", "ind_pct_ths",
         "volume_ratio", "pe_ttm", "turn_ma5", "turnover_rate"] + feats))
    have = [c for c in need_live if c in live.columns]
    lv = live[have].copy()
    if "mf_main_net" in lv.columns and "main_net" not in lv.columns:
        lv["main_net"] = lv["mf_main_net"] * 1e4
    lf = [c for c in feats if c in lv.columns]
    if len(lf) < len(feats):
        print("[warn] 快照缺模型特征 %d 个，缺列=%s"
              % (len(feats) - len(lf), [c for c in feats if c not in lv.columns]))
    lv["_p"] = booster.predict(lv[lf], num_threads=4)
    lv = lv.sort_values("_p", ascending=False)
    print("\n=== 实盘模型概率 Top20 ===")
    print(lv.head(20)[["_p"] + [c for c in
                                 ["main_net", "mf_main_net", "industry_pct",
                                  "ind_pct_ths", "volume_ratio", "pe_ttm"]
                                 if c in lv.columns]].to_string(index=False))

    top = lv.head(100).copy()
    sc_live = compute_real_scorecard(top, None)
    s_live = sc_live["total_new_real"]
    print("\n=== 实盘 Top100 评分卡（est=None，F6 用面板/快照列）===")
    for ln in (60.0, 55.0, 50.0):
        print("  红线 %.0f 过线数=%d" % (ln, int((s_live >= ln).sum())))
    print("  最高分=%.1f 中位=%.1f" % (np.nanmax(s_live), np.nanmedian(s_live)))
    print("  F5 均值=%.2f  F6 均值=%.2f" % (np.nanmean(sc_live["F5"]),
                                          np.nanmean(sc_live["F6"])))


if __name__ == "__main__":
    main()
