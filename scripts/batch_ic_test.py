# coding=utf-8
"""批量 IC 测试: alpha101 + gtja191 全量因子

目标: 从 302 个因子中筛选出 ICIR > 0.3 的高 alpha 因子
输出: reports/factor_ic_report.csv

用法: python scripts/batch_ic_test.py
"""
import sys
import os
import time
import traceback

# Add paths
sys.path.insert(0, 'E:/QuantLab')

import numpy as np
import pandas as pd
from pathlib import Path

# ============================================================
# 1. Load data
# ============================================================
print("=" * 60)
print("Batch IC Test: alpha101 + gtja191")
print("=" * 60)
print()

print("[1] Loading astock data...")
t0 = time.time()

df = pd.read_parquet('E:/astock/daily/stock_daily.parquet')
if isinstance(df.index, pd.MultiIndex):
    df = df.reset_index()

# Filter date range (2018-2026 for IC test)
df = df[df['trade_date'] >= pd.Timestamp('2018-01-01').date()]
df = df[df['trade_date'] <= pd.Timestamp('2026-06-30').date()]

# Filter valid stocks (has close, vol, amount)
df = df.dropna(subset=['close', 'vol', 'amount'])
df = df[df['close'] > 0]
df = df[df['vol'] > 0]

print("  Data: %d rows, %d stocks" % (len(df), df['ts_code'].nunique()))
print("  Time: %.1fs" % (time.time() - t0))

# ============================================================
# 2. Build panel (wide format for factors)
# ============================================================
print()
print("[2] Building panel...")
t0 = time.time()

# Pivot to wide format: index=date, columns=stock_code
trade_dates = sorted(df['trade_date'].unique())
all_codes = sorted(df['ts_code'].unique())

# Create panel dict (use 'volume' not 'vol' for factor compatibility)
panel = {}
col_map = {'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close',
            'vol': 'volume', 'amount': 'amount'}
for src_col, dst_col in col_map.items():
    if src_col in df.columns:
        pivot = df.pivot_table(index='trade_date', columns='ts_code', values=src_col)
        panel[dst_col] = pivot

print("  Panel: %d dates x %d stocks" % (len(trade_dates), len(all_codes)))
print("  Columns: %s" % list(panel.keys()))
print("  Time: %.1fs" % (time.time() - t0))

# ============================================================
# 3. Load factor registry
# ============================================================
print()
print("[3] Loading factor registry...")
t0 = time.time()

from backtest.factors.registry import Registry

registry = Registry()

# List all factors
alpha101_ids = registry.list(zoo='alpha101')
gtja191_ids = registry.list(zoo='gtja191')
all_ids = alpha101_ids + gtja191_ids

print("  alpha101: %d factors" % len(alpha101_ids))
print("  gtja191: %d factors" % len(gtja191_ids))
print("  Total: %d factors" % len(all_ids))
print("  Time: %.1fs" % (time.time() - t0))

# ============================================================
# 4. Compute IC/ICIR for each factor
# ============================================================
print()
print("[4] Computing IC/ICIR for each factor...")
print()

# Prepare forward returns (20-day)
print("  Preparing forward returns...")
close_panel = panel.get('close')
if close_panel is None:
    print("ERROR: No close data in panel")
    sys.exit(1)

fwd_ret = close_panel.pct_change(20).shift(-20)
print("  Forward returns ready")

# Monthly rebalance dates for IC calculation
monthly_dates = sorted(close_panel.index)
monthly_dates = [d for i, d in enumerate(monthly_dates) if i % 21 == 0]  # ~monthly
print("  IC dates: %d" % len(monthly_dates))

results = []
errors = []
skipped = []

for i, alpha_id in enumerate(all_ids):
    if (i + 1) % 10 == 0 or i == 0:
        print("  [%d/%d] Testing %s..." % (i + 1, len(all_ids), alpha_id))

    try:
        # Compute factor
        factor_df = registry.compute(alpha_id, panel)

        if factor_df is None or factor_df.empty:
            skipped.append((alpha_id, "empty result"))
            continue

        # Calculate IC for each date
        daily_ic = []
        for date in monthly_dates:
            if date not in factor_df.index or date not in fwd_ret.index:
                continue

            factor_vals = factor_df.loc[date].dropna()
            ret_vals = fwd_ret.loc[date].dropna()

            # Find common stocks
            common = factor_vals.index.intersection(ret_vals.index)
            if len(common) < 30:
                continue

            f = factor_vals[common]
            r = ret_vals[common]

            # Spearman rank IC
            ic = f.rank().corr(r.rank())
            if not np.isnan(ic):
                daily_ic.append(ic)

        if len(daily_ic) < 10:
            skipped.append((alpha_id, "insufficient IC dates (%d)" % len(daily_ic)))
            continue

        ic_mean = np.mean(daily_ic)
        ic_std = np.std(daily_ic)
        icir = ic_mean / ic_std if ic_std > 0 else 0
        ic_positive_pct = np.mean([1 if x > 0 else 0 for x in daily_ic])

        # Get metadata
        try:
            alpha = registry.get(alpha_id)
            theme = alpha.meta.get('theme', ['unknown'])
            nickname = alpha.meta.get('nickname', '')
        except:
            theme = ['unknown']
            nickname = ''

        results.append({
            'alpha_id': alpha_id,
            'zoo': 'alpha101' if 'alpha101' in alpha_id else 'gtja191',
            'nickname': nickname,
            'theme': ','.join(theme) if isinstance(theme, list) else str(theme),
            'ic_mean': round(ic_mean, 4),
            'ic_std': round(ic_std, 4),
            'icir': round(icir, 4),
            'ic_positive_pct': round(ic_positive_pct, 4),
            'n_dates': len(daily_ic),
        })

    except Exception as e:
        errors.append((alpha_id, str(e)[:100]))
        continue

print()
print("  Completed: %d factors" % len(results))
print("  Skipped: %d factors" % len(skipped))
print("  Errors: %d factors" % len(errors))

# ============================================================
# 5. Save results
# ============================================================
print()
print("[5] Saving results...")

# Save full report
results_df = pd.DataFrame(results)
results_df = results_df.sort_values('icir', ascending=False)

out_dir = 'E:/QuantLab/projects/Project_01_多因子IC小盘Alpha/reports'
os.makedirs(out_dir, exist_ok=True)

csv_path = os.path.join(out_dir, 'factor_ic_report.csv')
results_df.to_csv(csv_path, index=False, encoding='utf-8-sig')
print("  Full report: %s" % csv_path)

# Save top factors (ICIR > 0.3)
top_factors = results_df[results_df['icir'] > 0.3]
top_path = os.path.join(out_dir, 'top_factors.csv')
top_factors.to_csv(top_path, index=False, encoding='utf-8-sig')
print("  Top factors (ICIR>0.3): %d" % len(top_factors))

# Save summary
summary_path = os.path.join(out_dir, 'factor_ic_summary.md')
with open(summary_path, 'w', encoding='utf-8') as f:
    f.write("# Factor IC Test Summary\n\n")
    f.write("## Overview\n")
    f.write("- Total factors tested: %d\n" % len(all_ids))
    f.write("- Successful: %d\n" % len(results))
    f.write("- Skipped: %d\n" % len(skipped))
    f.write("- Errors: %d\n" % len(errors))
    f.write("\n## Top Factors (ICIR > 0.3)\n\n")
    f.write("| Rank | Alpha ID | Zoo | Theme | IC Mean | ICIR | IC+%% |\n")
    f.write("|------|----------|-----|-------|---------|------|------|\n")
    for idx, row in top_factors.iterrows():
        f.write("| %d | %s | %s | %s | %.4f | %.4f | %.1f%% |\n" % (
            len(results) - list(results_df.index).index(idx),
            row['alpha_id'], row['zoo'], row['theme'],
            row['ic_mean'], row['icir'], row['ic_positive_pct'] * 100))

    f.write("\n## Current Strategy Factors\n\n")
    f.write("| Factor | Weight | ICIR |\n")
    f.write("|--------|--------|------|\n")
    for factor_name in ['alpha101_016', 'alpha101_015', 'alpha101_013', 'alpha101_044',
                         'gtja191_032', 'gtja191_083']:
        match = results_df[results_df['alpha_id'] == factor_name]
        if len(match) > 0:
            icir = match.iloc[0]['icir']
        else:
            icir = 'N/A'
        f.write("| %s | - | %s |\n" % (factor_name, icir))

print("  Summary: %s" % summary_path)

# Print top 20
print()
print("=" * 60)
print("TOP 20 Factors by ICIR")
print("=" * 60)
print()
print("%-5s %-20s %-10s %-20s %8s %8s %8s" % (
    "Rank", "Alpha ID", "Zoo", "Theme", "IC Mean", "ICIR", "IC+%%"))
print("-" * 85)
for i, (_, row) in enumerate(top_factors.head(20).iterrows()):
    print("%-5d %-20s %-10s %-20s %8.4f %8.4f %7.1f%%" % (
        i + 1, row['alpha_id'], row['zoo'], row['theme'][:20],
        row['ic_mean'], row['icir'], row['ic_positive_pct'] * 100))

print()
print("=" * 60)
print("Batch IC Test Complete!")
print("=" * 60)
