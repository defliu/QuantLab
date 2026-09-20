# coding: utf-8
"""任务2：独立复算 09-18 三路候选（实盘/DE/历史三套规则）。

DE 独立核查 —— 不采信 TRAE probe_g2_rules_compare.py 输出，按源码逻辑自行实现：
  规则1 实盘 deploy_predict_g2.py：top100(prob) → 红线60 → top10 by total_new → 买 top2 by total_new
  规则2 DE de_r8_common.py     ：top100(prob) → 红线60 → top10 by prob      → 买 top2 by total
  规则3 历史 scan_rotate_cost_real.py：全市场 top10 by prob（不筛红线）→ 买入时筛红线 → 买 top2 by total_new

F2 口径说明：离线核查禁止触发行情连接（不用 g2_realtime/新浪），F2 用快照 mf_main_net*1e4
（deploy_predict_g2 L117-118 的回退口径）。total_new 数值允许与实盘 CSV 有 F2 源差异。
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

HERE = os.path.dirname(os.path.abspath(__file__))
SNAP = os.path.join(PROJ, "data_live", "g2_latest_features.parquet")
MODEL = r"D:\QuantLab\models\lgb_model_v3_g2_strong_real_20260915_1522t.txt"
META = os.path.join(PROJ, "data", "features_v3_g2_strong_real_20260915.json")
CSV_LIVE2 = os.path.join(PROJ, "data", "selections", "g2", "20260918_g2_top2.csv")
CSV_LIVE10 = os.path.join(PROJ, "data", "selections", "g2", "20260918_g2_top10.csv")

TARGET = pd.Timestamp("2026-09-18")
POOL_N, TOP_LIST, REDLINE, TOP_BUY = 100, 10, 60.0, 2

meta = json.load(open(META, encoding="utf-8"))
feats = meta["feature_cols"]

day = pd.read_parquet(SNAP)
day["trade_date"] = pd.to_datetime(day["trade_date"])
day = day[day["trade_date"] == TARGET].copy()
day["ts_code"] = day["ts_code"].astype(str)
print("[1] 快照 %s 全市场 %d 只, 特征 %d" % (TARGET.date(), len(day), len(feats)))

booster = lgb.Booster(model_file=MODEL)
day["prob"] = booster.predict(day[feats].astype("float32").values)
day["rank"] = day["prob"].rank(ascending=False, method="min").astype(int)

# est：主库 T 日 pe_ttm/turnover_rate（deploy_predict_g2 L100-110 写法）
try:
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["trade_date", "ts_code", "pe_ttm", "turnover_rate"])
    if "trade_date" not in daily.columns:
        raise KeyError("trade_date 不在数据列，回退旧布局")
except Exception:
    daily = pd.read_parquet(DC.MAIN_DAILY, columns=["pe_ttm", "turnover_rate"]).reset_index()
daily["trade_date"] = pd.to_datetime(daily["trade_date"])
daily["ts_code"] = daily["ts_code"].astype(str)
daily = daily.set_index(["trade_date", "ts_code"])
_n0918 = daily[daily.index.get_level_values("trade_date") == TARGET]
print("[2] 主库 est 09-18 行数 %d（0=F6 走缺失口径）" % len(_n0918))


def attach_score(pool):
    pool = pool.copy()
    for c in ["main_net", "industry_pct", "volume_ratio"]:
        if c not in pool.columns:
            pool[c] = np.nan
    if pool["main_net"].isna().all() and "mf_main_net" in pool.columns:
        pool["main_net"] = pool["mf_main_net"] * 1e4
    if pool["industry_pct"].isna().all() and "ind_pct_ths" in pool.columns:
        pool["industry_pct"] = pool["ind_pct_ths"]
    idx = pd.MultiIndex.from_arrays([pool["trade_date"], pool["ts_code"].astype(str)])
    est = daily.reindex(idx)
    sc = SR.compute_real_scorecard(pool, est)
    for f in ("F1", "F2", "F3", "F4", "F5", "F6"):
        pool["SC_" + f] = sc[f].values
    pool["total_new"] = sc["total_new_real"].values
    return pool


pool100 = day.nlargest(POOL_N, "prob")
pool100 = attach_score(pool100)
passed = pool100[pool100["total_new"] >= REDLINE].copy()
print("[3] top100 池内过红线(>=%.0f) %d 只" % (REDLINE, len(passed)))


def dump(tag, cand):
    cand = cand.copy()
    buy = cand.sort_values("total_new", ascending=False).head(TOP_BUY)
    print("\n===== %s：候选 %d 只, 买入 Top%d(by total_new) =====" % (tag, len(cand), TOP_BUY))
    show = cand.sort_values("total_new", ascending=False)[
        ["ts_code", "prob", "rank", "total_new", "SC_F1", "SC_F2", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]]
    print(show.to_string(index=False))
    print("  → 买入Top2 prob排名: %s" % " / ".join(str(int(r)) for r in buy["rank"]))
    return cand.sort_values("total_new", ascending=False), [int(r) for r in buy["rank"]]


# 规则1 实盘
live10, live_buy_ranks = dump("规则1 实盘 deploy_predict_g2（红线后 total_new top10）",
                              passed.nlargest(TOP_LIST, "total_new"))
# 规则2 DE
de10, de_buy_ranks = dump("规则2 DE de_r8_common（红线后 prob top10）",
                          passed.nlargest(TOP_LIST, "prob"))
# 规则3 历史
hist_raw = day.nlargest(TOP_LIST, "prob")
hist_scored = attach_score(hist_raw)
hist10 = hist_scored[hist_scored["total_new"] >= REDLINE]
_, hist_buy_ranks = dump("规则3 历史 scan_rotate_cost_real（全市场 prob top10 → 买入时红线）", hist10)

# 实盘实际落盘对照
print("\n===== 实盘实际落盘（data/selections/g2/20260918_g2_top2.csv / top10.csv）=====")
for p in (CSV_LIVE2, CSV_LIVE10):
    if os.path.exists(p):
        s = pd.read_csv(p)
        s["rank"] = s["ts_code"].astype(str).map(dict(zip(day["ts_code"], day["rank"])))
        s["prob_recalc"] = s["ts_code"].astype(str).map(dict(zip(day["ts_code"], day["prob"].round(4))))
        print("--", os.path.basename(p))
        print(s[["ts_code", "prob", "prob_recalc", "rank", "total_new"]].to_string(index=False))

# 落盘中间产物
out = {
    "n_all": int(len(day)),
    "n_passed_top100": int(len(passed)),
    "live_buy_ranks": live_buy_ranks,
    "de_buy_ranks": de_buy_ranks,
    "hist_buy_ranks": hist_buy_ranks,
    "hist_top10_passed": int(len(hist10)),
}
for name, df in (("rule1_live_top10", live10), ("rule2_de_top10", de10),
                 ("rule3_hist_top10_scored", hist_scored), ("passed_top100", passed)):
    df2 = df.copy()
    if "rank" in df2.columns:
        df2["rank"] = df2["rank"].astype(int)
    df2.to_csv(os.path.join(HERE, "task2_%s.csv" % name), index=False, encoding="utf-8-sig")
with open(os.path.join(HERE, "task2_summary.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n[done]", json.dumps(out, ensure_ascii=False))
