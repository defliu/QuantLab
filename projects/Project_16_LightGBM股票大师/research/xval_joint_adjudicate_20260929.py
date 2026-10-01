# -*- coding: utf-8 -*-
"""T-20260928-004 分歧终裁：F5 中性化反事实红线 60 命中数 DE=5 vs OP=4。

方法：按 opx_verify_20260928.py 的 A/B 段同款生产链路重算 Top100（快照 09-28 原版
未动），与 DE 中间产物 xval_de_p1_top100_20260928.csv 逐票 join 对比 total_new/SC_F5，
找出造成 4 vs 5 的票并给出裁决。只读，不写任何生产路径。
"""
import os
import sys

import numpy as np
import pandas as pd
import lightgbm as lgb

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DQL = r"D:\QuantLab"
sys.path.insert(0, PROJ)
sys.path.insert(0, DQL)

import data_config as DC  # noqa: E402
import scorecard_real as SR  # noqa: E402
import deploy_predict_g2 as G2  # noqa: E402

SNAP = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
DE_CSV = os.path.join(PROJ, "research", "xval_de_p1_top100_20260928.csv")
REDLINE = 60.0
PRE_POOL = 100

model_path, meta_path = DC.g2_live()
print("live 模型 = %s" % os.path.basename(model_path))
import json
with open(meta_path, "r", encoding="utf-8") as fh:
    _meta = json.load(fh)
feat_cols = _meta["feature_cols"] if isinstance(_meta, dict) else list(_meta)
print("meta 特征列数 = %d" % len(feat_cols))

day = pd.read_parquet(SNAP)
day["trade_date"] = pd.to_datetime(day["trade_date"])
target = day["trade_date"].max()
print("快照目标日 = %s，全量行数 = %d" % (target.date(), len(day)))

bst = lgb.Booster(model_file=model_path)
day["prob"] = bst.predict(day[feat_cols].astype("float32").values)
pre = day.nlargest(PRE_POOL, "prob").copy()

# F6 est：复刻 deploy_predict_g2 两段式布局探测
est = None
daily = None
try:
    daily = pd.read_parquet(DC.DAILY_BASIC,
                            columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
    if "trade_date" not in daily.columns:
        raise KeyError("trade_date 不在数据列，回退旧布局")
except Exception:
    daily = pd.read_parquet(DC.DAILY_BASIC,
                            columns=["pe_ttm", "turnover_rate"]).reset_index()
daily["trade_date"] = pd.to_datetime(daily["trade_date"])
daily["ts_code"] = daily["ts_code"].astype(str)
daily = daily.set_index(["trade_date", "ts_code"])
idx = pd.MultiIndex.from_arrays([pre["trade_date"], pre["ts_code"]])
cand = daily.reindex(idx)
hit = float(cand["pe_ttm"].notna().mean())
print("Top100 F6 est 命中率 = %.0f%%" % (hit * 100))
if hit >= 0.5:
    est = cand

# F2/F5 映射 + F3 补算（生产同款）
if "main_net" not in pre.columns and "mf_main_net" in pre.columns:
    pre["main_net"] = pre["mf_main_net"] * 1e4
if "industry_pct" not in pre.columns and "ind_pct_ths" in pre.columns:
    pre["industry_pct"] = pre["ind_pct_ths"]
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in pre.columns:
        pre[c] = np.nan
G2._fill_f3_events(pre, verbose=False)
sc = SR.compute_real_scorecard(pre, est)
pre["total_new"] = sc["total_new_real"].values
for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
    pre["SC_%s" % f] = sc[f].values

# 中性化反事实（两侧同款线性口径）
w5 = SR.SC_WEIGHTS["F5"]
delta = w5 * (5.0 - pre["SC_F5"]) * 10.0
pre["neu"] = pre["total_new"] + delta
print()
print("本次重算：实际 >=60 = %d 只；中性化后 >=60 = %d 只"
      % (int((pre["total_new"] >= REDLINE).sum()), int((pre["neu"] >= REDLINE).sum())))

cols = ["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5",
        "SC_F6", "neu"]
top = pre[pre["neu"] >= 57].sort_values("neu", ascending=False)
print()
print("中性化后 neu>=57 的票（本次重算）：")
print(top[cols].round(4).to_string(index=False))

# 与 DE 中间产物逐票对比
de = pd.read_csv(DE_CSV)
de_small = de[["ts_code", "prob", "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4",
               "SC_F5", "SC_F6"]].copy()
de_small.columns = ["ts_code"] + ["de_" + c for c in de_small.columns[1:]]
m = pre[cols].merge(de_small, on="ts_code", how="inner", suffixes=("", "_de"))
print()
print("join 上的票数 = %d / %d" % (len(m), PRE_POOL))
for c in ["prob", "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]:
    a = m[c].astype(float).values
    b = m["de_" + c].astype(float).values
    bad = ~np.isclose(a, b, rtol=0, atol=1e-6, equal_nan=True)
    if bad.any():
        print("!! 字段 %s 不一致 %d 只：" % (c, int(bad.sum())))
        print(m.loc[bad, ["ts_code", c, "de_" + c, "SC_F5", "neu"]].round(4).to_string(index=False))
    else:
        print("字段 %s：与 DE 逐票一致" % c)

# DE 侧自己的中性化命中数（按 DE 数据独立算）
de["neu_de"] = de["total_new"] + w5 * (5.0 - de["SC_F5"]) * 10.0
print()
print("按 DE 数据独立复算：实际 >=60 = %d 只；中性化后 >=60 = %d 只"
      % (int((de["total_new"] >= REDLINE).sum()), int((de["neu_de"] >= REDLINE).sum())))
print("DE 中性化后 >=60 名单：")
print(de.loc[de["neu_de"] >= REDLINE, ["ts_code", "total_new", "SC_F5", "neu_de"]]
      .round(4).to_string(index=False))
