# coding: utf-8
"""探针：验证实盘 G2 选股 CSV 中每只票在当天全市场的模型 prob 排名。
判断「实盘按 total 排序买入是否包含 prob 深挖票（排名 11~100）」。
只读面板/模型/选股 CSV，纯推理，不触发行情/交易。
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)

PANEL = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260915.json")
MODEL_1522T = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
MODEL_1964T = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260825_1964t.txt"
SEL_DIR = os.path.join(PROJ, "data", "selections", "g2")

meta = json.load(open(META, encoding="utf-8"))
feats = meta["feature_cols"]
print("[meta] %d 特征" % len(feats))

# 实盘选中日期（CSV 数据日）与当时运行的模型
# 1522t 于 09-15 promote，之前实盘跑 1964t
check_dates = [
    ("20260918", "1522t"), ("20260917", "1522t"), ("20260916", "1522t"),
    ("20260915", "1522t"), ("20260914", "1964t"), ("20260911", "1964t"),
    ("20260910", "1964t"), ("20260909", "1964t"), ("20260908", "1964t"),
]

panel = pd.read_parquet(PANEL, columns=feats + ["ts_code", "trade_date"])
panel["trade_date"] = pd.to_datetime(panel["trade_date"])
need = set(d for d, _ in check_dates)
panel = panel[panel["trade_date"].isin(pd.to_datetime(list(need)))]

boosters = {
    "1522t": lgb.Booster(model_file=MODEL_1522T),
    "1964t": lgb.Booster(model_file=MODEL_1964T),
}

for ds, tag in check_dates:
    day = panel[panel["trade_date"] == pd.Timestamp(ds)].copy()
    if day.empty:
        print("\n=== %s %s 面板无数据 ===" % (ds, tag))
        continue
    day["prob"] = boosters[tag].predict(day[feats].astype("float32").values)
    day["rank"] = day["prob"].rank(ascending=False, method="min")
    n_all = len(day)
    csv_t10 = os.path.join(SEL_DIR, "%s_g2_top10.csv" % ds)
    if not os.path.exists(csv_t10):
        print("\n=== %s 无 top10 CSV ===" % ds)
        continue
    sel = pd.read_csv(csv_t10)
    print("\n=== %s (%s) 全市场 %d 只 ===" % (ds, tag, n_all))
    for _, r in sel.iterrows():
        code = str(r["ts_code"])
        sub = day[day["ts_code"].astype(str) == code]
        if sub.empty:
            print("  %-10s prob=%.4f total=%6.1f  -> 面板无此票!" % (code, r["prob"], r["total_new"]))
            continue
        rk = int(sub["rank"].iloc[0])
        pv = sub["prob"].iloc[0]
        band = "PROB-TOP10" if rk <= 10 else ("PROB-11..100" if rk <= 100 else "PROB>100")
        print("  %-10s prob=%.4f total=%6.1f  -> 全市场 prob 排名 %4d  [%s]" % (code, r["prob"], r["total_new"], rk, band))
    # top2 实盘实际买入
    csv_t2 = os.path.join(SEL_DIR, "%s_g2_top2.csv" % ds)
    if os.path.exists(csv_t2):
        s2 = pd.read_csv(csv_t2)
        codes2 = s2["ts_code"].astype(str).tolist()
        print("  >> 实盘买入 TOP2: %s" % codes2)
