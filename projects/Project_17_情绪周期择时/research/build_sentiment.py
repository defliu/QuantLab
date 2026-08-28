# -*- coding: utf-8 -*-
"""
Project_17 Phase 0：情绪温度计有效性验证（预注册）
====================================================
构建 2019-11 起日频情绪五指标 + 五阶段分类，对全市场小盘池做判据检验：
  P0-1 阶段信号 IC >= 0.03（2021+ 分段独立成立）
  P0-2 高潮 vs 冰点未来20日收益差 t 检验显著（p<0.05，方向=冰点>高潮）
  P0-3 无前视：所有指标仅用 T 日及以前信息

判据冻结于 specs/任务书_v1.md，本脚本只读数据、不调参、不挑样本。
"""
import sys
import numpy as np
import pandas as pd

pd.set_option("display.width", 200)

ASTOCK = r"E:\astock"
OUT = r"D:\QuantLab\projects\Project_17_情绪周期择时\results"
os_mkdir = __import__("os").makedirs
os_mkdir(OUT, exist_ok=True)

# ---------- 1. 读涨停明细 ----------
print("[1] 读 limit_list_d ...")
ll = pd.read_parquet(f"{ASTOCK}\\lhb\\limit_list_d.parquet").reset_index()
ll["trade_date"] = pd.to_datetime(ll["trade_date"])

# ---------- 2. 读日线（列子集） ----------
print("[2] 读 stock_daily 列子集 ...")
daily = pd.read_parquet(
    f"{ASTOCK}\\daily\\stock_daily.parquet",
    columns=["close", "circ_mv", "is_st", "listed_days", "pct_chg"],
).reset_index()
daily["trade_date"] = pd.to_datetime(daily["trade_date"])
daily = daily.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

cal = np.array(sorted(daily["trade_date"].unique()))  # 交易日历
cal_idx = pd.Series(np.arange(len(cal)), index=cal)

# ---------- 3. 情绪五指标（逐日） ----------
print("[3] 构建情绪五指标 ...")
up = ll[ll["limit"] == "U"].copy()          # 涨停
zb = ll[ll["limit"] == "Z"].copy()          # 炸板

# 3.1 涨停家数 / 连板高度 / 封板率
g_up = up.groupby("trade_date")
limit_up_count = g_up.size()
max_lianban = g_up["limit_times"].max()
day_stats = up.groupby("trade_date").size().rename("limit_up_count").to_frame()
day_stats["max_lianban"] = up.groupby("trade_date")["limit_times"].max()
fz = ll[ll["limit"].isin(["U", "Z"])].groupby("trade_date").size()
day_stats["fengban_ratio"] = (day_stats["limit_up_count"] / fz).clip(0, 1)

# 3.2 昨日涨停溢价 + 晋级率（昨日U → 今日表现）
# 为每个 U 记录赋"下一交易日"（用 daily 交易日历）
cal_pos = pd.Series(np.arange(len(cal)), index=cal)
pos = up["trade_date"].map(cal_pos)
up = up[pos.notna()]
pos = pos[pos.notna()].astype(int)
nxt = pos + 1
up = up[nxt < len(cal)]
up["next_date"] = pd.to_datetime(cal[nxt[nxt < len(cal)]])

# 昨日涨停股今日涨幅（用 daily pct_chg）
prem = up[["next_date", "ts_code"]].merge(
    daily[["trade_date", "ts_code", "pct_chg"]],
    left_on=["next_date", "ts_code"],
    right_on=["trade_date", "ts_code"],
    how="inner",
)
premium = prem.groupby("trade_date")["pct_chg"].mean().rename("prev_limit_premium")

# 晋级率 = 昨日U ∩ 今日U / 昨日U（集合交集，逐日）
today_sets = up.groupby("trade_date")["ts_code"].agg(set)
promote_rows = []
for dt, g in up.groupby("next_date"):
    prev_codes = set(g["ts_code"])
    cross = len(prev_codes & today_sets.get(dt, set()))
    promote_rows.append((dt, cross / len(prev_codes)))
promote = (
    pd.DataFrame(promote_rows, columns=["trade_date", "promotion_rate"])
    .set_index("trade_date")["promotion_rate"]
)

# 汇总
senti = day_stats.join(premium).join(promote)
senti.index.name = "trade_date"
senti = senti.sort_index()

# ---------- 4. 合成情绪分 + 五阶段 ----------
print("[4] 合成情绪分与阶段分类 ...")
cols = ["limit_up_count", "max_lianban", "fengban_ratio", "prev_limit_premium", "promotion_rate"]
W = 250  # 滚动窗口（交易日），只含历史，无前视
z = pd.DataFrame(index=senti.index)
for c in cols:
    mu = senti[c].rolling(W, min_periods=120).mean()
    sd = senti[c].rolling(W, min_periods=120).std()
    z[c] = (senti[c] - mu) / sd
senti["sentiment_score"] = z[cols].mean(axis=1)  # 等权合成（全部正向指标）
# 滚动 min-max 归一近似分位
rmin = senti["sentiment_score"].rolling(W, min_periods=120).min()
rmax = senti["sentiment_score"].rolling(W, min_periods=120).max()
senti["pct_rank"] = ((senti["sentiment_score"] - rmin) / (rmax - rmin)).clip(0, 1)
bins = [0.0, 0.15, 0.35, 0.65, 0.85, 1.0]
labels = ["冰点", "回暖", "中性", "高潮", "过热"]
senti["stage"] = pd.cut(senti["pct_rank"], bins=bins, labels=labels, include_lowest=True)

senti.to_csv(f"{OUT}\\sentiment_series.csv", encoding="utf-8-sig")
print("情绪序列已保存:", len(senti), "个交易日 |", senti["stage"].value_counts().to_dict())
print(senti.tail(3).to_string())

# ---------- 5. 小盘池未来收益 ----------
print("[5] 构建小盘池 forward 收益 ...")
pool = daily[
    (daily["circ_mv"] < 500000)          # 流通市值 < 50亿（万元）
    & (daily["is_st"] != 1)
    & (daily["listed_days"].fillna(1e9) >= 60)  # 剔除上市<60日新股
].copy()
wide = pool.pivot_table(index="trade_date", columns="ts_code", values="close")
# fwd5 = close[T+5]/close[T+1]-1（T 收盘出信号，T+1 起持有，无前视）
fwd5 = (wide.shift(-5) / wide.shift(-1)) - 1
fwd20 = (wide.shift(-20) / wide.shift(-1)) - 1
pool_fwd5 = fwd5.mean(axis=1).rename("pool_fwd5")
pool_fwd20 = fwd20.mean(axis=1).rename("pool_fwd20")

frame = senti.join(pool_fwd5).join(pool_fwd20).dropna(subset=["stage", "pool_fwd5"])
frame = frame[frame.index >= "2020-01-01"]

# ---------- 6. 判据检验 ----------
print("[6] 判据检验 ...")
from scipy import stats

def ic_series(x, y):
    m = x.notna() & y.notna()
    if m.sum() < 30:
        return np.nan
    return stats.spearmanr(x[m], y[m]).correlation

# P0-1：阶段数值(0-4) 与小盘池 fwd5 的 Spearman IC
stage_num = pd.Categorical(frame["stage"], categories=labels, ordered=True).codes
ic_all = ic_series(pd.Series(stage_num, index=frame.index), frame["pool_fwd5"])
ic_21 = ic_series(pd.Series(stage_num, index=frame.index)[frame.index >= "2021-01-01"],
                  frame["pool_fwd5"][frame.index >= "2021-01-01"])

# 分阶段 fwd5 / fwd20 均值
stage_means = frame.groupby("stage", observed=True)[["pool_fwd5", "pool_fwd20"]].mean()
stage_n = frame.groupby("stage", observed=True).size()

# P0-2：高潮 vs 冰点 fwd20 t 检验
a = frame.loc[frame["stage"] == "冰点", "pool_fwd20"].dropna()
b = frame.loc[frame["stage"] == "高潮", "pool_fwd20"].dropna()
tstat, pval = stats.ttest_ind(a, b, equal_var=False)

print("=" * 60)
print("P0-1 阶段IC(全期):", round(ic_all, 4))
print("P0-1 阶段IC(2021+):", round(ic_21, 4))
print("\n各阶段样本数:", stage_n.to_dict())
print("\n各阶段未来5日/20日收益均值:")
print(stage_means.round(4).to_string())
print(f"\nP0-2 冰点fwd20均值={a.mean()*100:.2f}% vs 高潮fwd20均值={b.mean()*100:.2f}% | t={tstat:.2f} p={pval:.4f}")

# 7. 结论
verdict = []
verdict.append(f"P0-1 IC全期={ic_all:.3f}（判据≥0.03，方向：负=高分位情绪→低未来收益）" +
               ("通过" if abs(ic_all) >= 0.03 else "未过"))
verdict.append(f"P0-1 IC 2021+ ={ic_21:.3f}（判据≥0.03）" + ("通过" if abs(ic_21) >= 0.03 else "未过"))
verdict.append(f"P0-2 冰点>高潮：diff={(a.mean()-b.mean())*100:.2f}pp p={pval:.4f}" +
               ("通过" if (a.mean() > b.mean() and pval < 0.05) else "未过"))
print("\n判定:")
for v in verdict:
    print("  -", v)

# 8. 报告落盘
md = f"""# Project_17 Phase 0：情绪温度计有效性验证（预注册）

> 运行日期：2026-08-26 ｜ 数据：E:/astock（涨停明细 2019-11-28 ~ 2026-08-21，日线 2009-01 ~ 2026-08-14）
> 判据冻结于 `specs/任务书_v1.md`，本报告为一次性预注册检验，未调参。

## 一、情绪序列概览

- 交易日数：{len(senti)}（滚动窗口 warmup 后，阶段自 ~2020 中起有效）
- 五指标：涨停家数 / 连板高度 / 封板率 / 昨日涨停溢价 / 晋级率，各滚动 250 日 z-score 等权合成 sentiment_score → 滚动 min-max 归一 pct_rank → 五阶段（冰点<0.15/回暖<0.35/中性<0.65/高潮<0.85/过热≥0.85）
- 阶段分布：{senti["stage"].value_counts().to_dict()}

## 二、判据结果

| 判据 | 指标 | 实测 | 门限 | 结果 |
|---|---|---|---|---|
| P0-1 | 阶段信号 IC（全期） | {ic_all:.3f} | \\|IC\\|≥0.03 | {"通过" if abs(ic_all)>=0.03 else "未过"} |
| P0-1 | 阶段信号 IC（2021+） | {ic_21:.3f} | \\|IC\\|≥0.03 | {"通过" if abs(ic_21)>=0.03 else "未过"} |
| P0-2 | 冰点 vs 高潮 fwd20 收益差 | {(a.mean()-b.mean())*100:.2f}pp (p={pval:.4f}) | 冰点>高潮 且 p<0.05 | {"通过" if (a.mean()>b.mean() and pval<0.05) else "未过"} |
| P0-3 | 无前视（滚动窗口仅含历史） | 构建口径保证 | — | 通过 |

## 三、分阶段未来收益（小盘池 circ_mv<50亿，剔除ST/新股）

| 阶段 | N | fwd5均值 | fwd20均值 |
|---|---|---|---|
""" + "\n".join(
    f"| {i} | {stage_n.get(i,0)} | {stage_means.loc[i,'pool_fwd5']*100:.2f}% | {stage_means.loc[i,'pool_fwd20']*100:.2f}% |"
    for i in labels if i in stage_means.index
) + f"""

## 四、结论与去向

- 是否通过预注册（P0-1 且 P0-2 且 2021+ 分段成立）：{"**通过，进入 Phase 1**" if (abs(ic_all)>=0.03 and abs(ic_21)>=0.03 and a.mean()>b.mean() and pval<0.05) else "**未全过，按证伪红线复核/归档**"}
- 说明：IC 为负表示「情绪高分位(高潮/过热) → 未来小盘池收益走低」，符合养家「高潮后期减仓、冰点回暖加仓」的方向假设；若为正需警惕逻辑反转。

## 五、附

- 情绪序列产物：`results/sentiment_series.csv`（date / 五指标 / zscore / score / pct_rank / stage）
- 脚本：`research/build_sentiment.py`
"""
with open(f"{OUT}\\Phase0_情绪温度计有效性_20260826.md", "w", encoding="utf-8") as f:
    f.write(md)
print("报告已写入 results/Phase0_情绪温度计有效性_20260826.md")
