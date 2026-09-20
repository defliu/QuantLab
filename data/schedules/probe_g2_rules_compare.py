# coding: utf-8
"""探针：09-18 实盘快照下三套候选构造规则对比（实盘 deploy_predict_g2 / DE de_r8_common / 历史 scan_rotate_cost_real）。

诚哥要求重点复核 deploy_predict_g2.py 候选构造逻辑。本脚本用真实实盘快照 + 真实 live 模型 + 真实评分卡，
对同一交易日(2026-09-18)分别按三套规则生成候选，对比「候选池组成」与「买入 Top2」。
只读快照/模型/主库；纯推理，不触发行情/交易。
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

import data_config as DC     # noqa: E402
import scorecard_real as SR  # noqa: E402

SNAP = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260915.json")
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
SEL_DIR = os.path.join(PROJ, "data", "selections", "g2")

TARGET = pd.Timestamp("2026-09-18")
POOL_N, TOP_LIST, REDLINE, TOP2 = 100, 10, 60.0, 2

meta = json.load(open(META, encoding="utf-8"))
feats = meta["feature_cols"]

day = pd.read_parquet(SNAP)
day["trade_date"] = pd.to_datetime(day["trade_date"])
day = day[day["trade_date"] == TARGET].copy()
print("[快照] %s 全市场 %d 只, 特征 %d" % (TARGET.date(), len(day), len(feats)))

# 模型推理
booster = lgb.Booster(model_file=MODEL)
day["prob"] = booster.predict(day[feats].astype("float32").values)
day["rank"] = day["prob"].rank(ascending=False, method="min")

# est: 主库 T 日 pe_ttm/turnover_rate（deploy_predict_g2 同款，F6 用）
try:
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
    if "trade_date" not in daily.columns:
        raise KeyError("trade_date 不在数据列，回退旧布局")
except Exception:
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
daily["trade_date"] = pd.to_datetime(daily["trade_date"])
daily["ts_code"] = daily["ts_code"].astype(str)
daily = daily.set_index(["trade_date", "ts_code"])

# F2/F5 列兜底（快照列名兼容）
if "main_net" not in day.columns and "mf_main_net" in day.columns:
    day["main_net"] = day["mf_main_net"] * 1e4
if "industry_pct" not in day.columns and "ind_pct_ths" in day.columns:
    day["industry_pct"] = day["ind_pct_ths"]
for c in ["main_net", "industry_pct", "volume_ratio"]:
    if c not in day.columns:
        day[c] = np.nan

# 评分卡（deploy 只对 top100 池算；此处为对齐口径也只对 top100）
pool100 = day.nlargest(POOL_N, "prob").copy()
idx = pd.MultiIndex.from_arrays([pool100["trade_date"], pool100["ts_code"].astype(str)])
est = daily.reindex(idx)
pool100["total_new"] = SR.compute_real_scorecard(pool100, est)["total_new_real"].values
passed = pool100[pool100["total_new"] >= REDLINE].copy()
print("[红线] top100 池内过线 %d 只 (total>=%.0f)" % (len(passed), REDLINE))

def show(tag, df, k=TOP2):
    df = df.copy()
    df = df.sort_values("total_new", ascending=False)
    print("\n===== %s（候选 %d 只，买入 Top%d 按 total_new）=====" % (tag, len(df), k))
    for i, (_, r) in enumerate(df.head(k).iterrows(), 1):
        print("  买入#%d %-10s prob=%.4f prob排名=%3d total=%.1f" % (
            i, r["ts_code"], r["prob"], int(r["rank"]), r["total_new"]))
    for i, (_, r) in enumerate(df.iterrows(), 1):
        mark = "  <=" if i <= k else ""
        if i <= k or int(r["rank"]) <= 10:
            print("  [%2d] %-10s prob=%.4f prob排名=%3d total=%.1f%s" % (
                i, r["ts_code"], r["prob"], int(r["rank"]), r["total_new"], mark))

# ---- 规则1：实盘 deploy_predict_g2（红线后按 total_new 取 top10 → 按 total_new 买 top2）----
rule_live = passed.nlargest(TOP_LIST, "total_new")
show("实盘 deploy_predict_g2 红线后 total_new top10", rule_live, TOP2)

# ---- 规则2：DE de_r8_common（红线后按 prob 取 top10 → 按 total 排序买 top2）----
rule_de = passed.nlargest(TOP_LIST, "prob")
show("DE de_r8_common 红线后 prob top10", rule_de, TOP2)

# ---- 规则3：历史 scan_rotate_cost_real（全市场 prob top10 → 买入时红线）----
rule_hist = day.nlargest(TOP_LIST, "prob")
# 历史买入时红线：取 top10∩红线 按 total 排序
rule_hist = rule_hist.copy()
idx3 = pd.MultiIndex.from_arrays([rule_hist["trade_date"], rule_hist["ts_code"].astype(str)])
est3 = daily.reindex(idx3)
rule_hist["total_new"] = SR.compute_real_scorecard(rule_hist, est3)["total_new_real"].values
rule_hist = rule_hist[rule_hist["total_new"] >= REDLINE]
show("历史 scan_rotate_cost_real 全市场 prob top10（买入时红线）", rule_hist, TOP2)

# ---- 实盘实际落盘 Top2（含持仓排除后）----
csv2 = os.path.join(SEL_DIR, "20260918_g2_top2.csv")
s2 = pd.read_csv(csv2)
print("\n===== 实盘实际落盘买入 Top2（rebalance 排除持仓后）=====")
print(s2[["ts_code", "prob", "total_new"]].to_string(index=False))
