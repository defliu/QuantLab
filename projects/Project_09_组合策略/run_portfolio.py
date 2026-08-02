# coding=utf-8
"""组合回测：70%红利低波 + 30%质量小市值"""

import pandas as pd
import numpy as np
from pathlib import Path

# 加载两个策略的净值
div = pd.read_csv('E:/QuantLab/projects/Project_05_红利低波/results/dividend_equity.csv')
div['date'] = pd.to_datetime(div['date'])
div = div.set_index('date')['value']

sc = pd.read_csv('E:/QuantLab/projects/Project_06_质量小市值/results/smallcap_equity.csv')
sc['date'] = pd.to_datetime(sc['date'])
sc = sc.set_index('date')['value']

# 对齐日期
common_dates = div.index.intersection(sc.index)
div = div.loc[common_dates]
sc = sc.loc[common_dates]

# 归一化到1
div_norm = div / div.iloc[0]
sc_norm = sc / sc.iloc[0]

# 组合净值：70%红利低波 + 30%质量小市值
portfolio = 0.70 * div_norm + 0.30 * sc_norm

# 计算指标
total_ret = portfolio.iloc[-1] - 1
years = max(len(portfolio) / 252, 1/12)
ann_ret = (1 + total_ret) ** (1/years) - 1
max_dd = (portfolio / portfolio.cummax() - 1).min()
daily_ret = portfolio.pct_change().dropna()
sharpe = np.sqrt(252) * (daily_ret - 0.025/252).mean() / daily_ret.std() if daily_ret.std() > 0 else 0
calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

# 分年统计
portfolio_df = pd.DataFrame({'value': portfolio})
portfolio_df['year'] = portfolio_df.index.year

print("=" * 60)
print("组合回测：70%红利低波 + 30%质量小市值")
print("=" * 60)
print(f"回测区间: {common_dates[0].date()} ~ {common_dates[-1].date()}")
print()
print("整体指标:")
print(f"  总收益: {total_ret:.1%}")
print(f"  年化收益: {ann_ret:.1%}")
print(f"  最大回撤: {max_dd:.1%}")
print(f"  夏普比率: {sharpe:.2f}")
print(f"  Calmar比率: {calmar:.2f}")
print()
print("分年表现:")
for year in sorted(portfolio_df['year'].unique()):
    year_data = portfolio_df[portfolio_df['year'] == year]
    if len(year_data) > 1:
        yr_ret = (year_data['value'].iloc[-1] / year_data['value'].iloc[0] - 1) * 100
        yr_dd = ((year_data['value'] / year_data['value'].cummax()) - 1).min() * 100
        print(f"  {year}年: 收益{yr_ret:.1f}%, 回撤{yr_dd:.1f}%")

print()
print("对比单策略:")
print(f"  红利低波年化: {(div.iloc[-1]/div.iloc[0])**(1/years)-1:.1%}")
print(f"  质量小市值年化: {(sc.iloc[-1]/sc.iloc[0])**(1/years)-1:.1%}")
print(f"  组合年化: {ann_ret:.1%}")

# 保存结果
portfolio_df[['value']].to_csv('E:/QuantLab/projects/Project_09_组合策略/results/portfolio_70_30.csv')
print(f"\n结果已保存: E:/QuantLab/projects/Project_09_组合策略/results/")
