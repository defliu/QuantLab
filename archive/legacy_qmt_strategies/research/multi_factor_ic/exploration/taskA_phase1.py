# coding=utf-8
"""Phase1: 行业暴露分析
预计耗时: <30秒
"""
import sys, os
sys.path.insert(0, 'D:/QMT_STRATEGIES')
os.chdir('D:/QMT_STRATEGIES')
import pandas as pd
import numpy as np

OUT = "D:/QMT_STRATEGIES/research/multi_factor_ic/reports/v3_optimize"

# 1. 加载数据
print("[Phase1] 加载持仓明细...")
detail = pd.read_csv(f"{OUT}/holdings_detail.csv")
print(f"  持仓明细: {len(detail)} 行, {detail['rebalance_date'].nunique()} 调仓日")

# 2. 行业持仓占比
print("[Phase1] 计算行业持仓占比...")
industry_weight = detail.groupby('industry')['weight'].sum().sort_values(ascending=False)
industry_weight = industry_weight / industry_weight.sum()  # 归一化

# Top10行业占比
top10_ind = industry_weight.head(10)
top10_pct = top10_ind.sum() * 100
print(f"  Top10行业占比: {top10_pct:.1f}%")

# Herfindahl指数
hhi = (industry_weight ** 2).sum()
print(f"  行业Herfindahl指数: {hhi:.4f} (<0.05为分散)")

# 3. 行业收益贡献（计算各行业历史平均收益）
print("[Phase1] 计算行业收益贡献...")
panel = pd.read_parquet(f"{OUT}/cache/panel.parquet")

# 提取月度收益
pct_chg = panel['pct_chg'].unstack(level=1) / 100  # 转换为小数
pct_chg.index = pd.to_datetime(pct_chg.index)  # 确保索引是DatetimeIndex
monthly_ret = (1 + pct_chg).resample('ME').prod() - 1

# 计算各行业平均月收益
industry_codes = detail.groupby('industry')['stock_code'].unique()
industry_avg_ret = {}
for ind, codes in industry_codes.items():
    valid_codes = [c for c in codes if c in monthly_ret.columns]
    if len(valid_codes) >= 3:
        industry_avg_ret[ind] = monthly_ret[valid_codes].mean(axis=1).mean()

# 收益贡献 = 持仓权重 * 行业平均收益
contribution = {}
for ind in industry_weight.index:
    w = industry_weight.loc[ind]
    r = industry_avg_ret.get(ind, 0)
    contribution[ind] = w * r * 100  # 转换为百分比

contribution_df = pd.Series(contribution).sort_values(ascending=False)
top5_contrib = contribution_df.head(5)
print(f"  Top5收益贡献行业:\n{top5_contrib.to_string()}")

# 4. 保存结果
result_df = pd.DataFrame({
    'industry': industry_weight.index,
    'weight_pct': industry_weight.values * 100,
    'contribution_pct': [contribution.get(i, 0) for i in industry_weight.index],
    'stock_count': detail.groupby('industry')['stock_code'].nunique().reindex(industry_weight.index).values,
})
result_df.to_csv(f"{OUT}/industry_exposure.csv", index=False, encoding="utf-8-sig")

# 5. 输出简要结论
print("")
print("="*60)
print("Phase1 行业暴露分析结论")
print("="*60)
print(f"✅ Top10行业占比: {top10_pct:.1f}%")
print(f"✅ Herfindahl指数: {hhi:.4f} {'(行业分散)' if hhi < 0.05 else '(行业集中)'}")
print(f"✅ Top1收益贡献行业: {top5_contrib.index[0]} ({top5_contrib.iloc[0]:.2f}%)")
print(f"📦 交付物已保存: {OUT}/industry_exposure.csv")
