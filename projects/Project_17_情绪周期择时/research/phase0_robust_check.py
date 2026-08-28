# -*- coding: utf-8 -*-
"""
Project_17 Phase 0 稳健性检查（证伪前排除实现假象）
1) 连续 sentiment_score vs fwd5/fwd20 的 Spearman（比阶段哑变量更细）
2) 各指标单独 vs 未来收益 Spearman（找单一有效信号）
3) 滚动分位(quantile)重切阶段，重跑 P0-2（排除 min-max 归一化假象）
4) prev_limit_premium/promotion_rate 的 NaN 覆盖审计（排除 join bug）
"""
import numpy as np
import pandas as pd
from scipy import stats

OUT = r"D:\QuantLab\projects\Project_17_情绪周期择时\results"
senti = pd.read_csv(f"{OUT}\\sentiment_series.csv", index_col=0, parse_dates=True)
pool = pd.read_csv(f"{OUT}\\pool_fwd_ret.csv", index_col=0, parse_dates=True) \
    if __import__("os").path.exists(f"{OUT}\\pool_fwd_ret.csv") else None

print("[0] NaN 覆盖审计（情绪五指标）")
for c in ["limit_up_count", "max_lianban", "fengban_ratio", "prev_limit_premium", "promotion_rate"]:
    nn = senti[c].notna().mean()
    print(f"  {c}: 非NaN占比 {nn:.1%}")

# 重建 pool_fwd（若未缓存则快速重建）
ASTOCK = r"E:\astock"
if pool is None:
    daily = pd.read_parquet(f"{ASTOCK}\\daily\\stock_daily.parquet",
                            columns=["close", "circ_mv", "is_st", "listed_days"]).reset_index()
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    p = daily[(daily["circ_mv"] < 500000) & (daily["is_st"] != 1)
              & (daily["listed_days"].fillna(1e9) >= 60)]
    wide = p.pivot_table(index="trade_date", columns="ts_code", values="close")
    pool = pd.DataFrame({
        "pool_fwd5": (wide.shift(-5) / wide.shift(-1) - 1).mean(axis=1),
        "pool_fwd20": (wide.shift(-20) / wide.shift(-1) - 1).mean(axis=1),
    })
    pool.index.name = "trade_date"
    pool.to_csv(f"{OUT}\\pool_fwd_ret.csv", encoding="utf-8-sig")

f = senti.join(pool).dropna(subset=["sentiment_score"])
f = f[f.index >= "2020-01-01"]
f21 = f[f.index >= "2021-01-01"]

def sp(x, y):
    m = x.notna() & y.notna()
    if m.sum() < 30:
        return np.nan
    return stats.spearmanr(x[m], y[m]).correlation

print("\n[1] 连续 sentiment_score vs 未来收益 Spearman")
print(f"  score vs fwd5  全期 {sp(f['sentiment_score'], f['pool_fwd5']):+.4f} | 2021+ {sp(f21['sentiment_score'], f21['pool_fwd5']):+.4f}")
print(f"  score vs fwd20 全期 {sp(f['sentiment_score'], f['pool_fwd20']):+.4f} | 2021+ {sp(f21['sentiment_score'], f21['pool_fwd20']):+.4f}")

print("\n[2] 各指标单独 vs fwd5/fwd20 Spearman（全期 / 2021+）")
for c in ["limit_up_count", "max_lianban", "fengban_ratio", "prev_limit_premium", "promotion_rate"]:
    r5, r5_21, r20, r20_21 = (
        sp(f[c], f["pool_fwd5"]), sp(f21[c], f21["pool_fwd5"]),
        sp(f[c], f["pool_fwd20"]), sp(f21[c], f21["pool_fwd20"]),
    )
    print(f"  {c}: fwd5 {r5:+.4f}/{r5_21:+.4f} | fwd20 {r20:+.4f}/{r20_21:+.4f}")

print("\n[3] 滚动分位重切阶段（quantile 0.15/0.35/0.65/0.85）重跑 P0-2")
sc = f["sentiment_score"]
# 滚动 250 日分位（近似：rolling rank 用 rolling apply 对窗口内求 percentile）
q = sc.rolling(250, min_periods=120).apply(lambda x: (x[-1] <= x).mean(), raw=True)
f["pct_q"] = q.clip(0, 1)
labs = ["冰点", "回暖", "中性", "高潮", "过热"]
f["stage_q"] = pd.cut(f["pct_q"], [0, 0.15, 0.35, 0.65, 0.85, 1.0], labels=labs, include_lowest=True)
g = f.dropna(subset=["stage_q", "pool_fwd20"])
print("  分布:", g["stage_q"].value_counts().to_dict())
a = g.loc[g["stage_q"] == "冰点", "pool_fwd20"]
b = g.loc[g["stage_q"] == "高潮", "pool_fwd20"]
t, p = stats.ttest_ind(a, b, equal_var=False)
print(f"  冰点fwd20={a.mean()*100:.2f}% vs 高潮={b.mean()*100:.2f}% | t={t:.2f} p={p:.4f}")
gm = g.groupby("stage_q", observed=True)["pool_fwd20"].mean()
print("  分位重切后各阶段 fwd20:")
print(gm.round(4).to_string())
