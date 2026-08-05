# coding=utf-8
"""IC 测试框架：Rank IC、ICIR、分组收益。"""

import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

import numpy as np
import pandas as pd
from research.multi_factor_ic.factors import winsorize, standardize, FACTOR_CONFIG, compute_all_factors
from research.multi_factor_ic.data_loader import get_monthly_rebalance_dates
from research.multi_factor_ic.config import OUTPUT_DIR, WINSORIZE_PCT
from pathlib import Path


def calc_forward_return(panel, date, hold_days=20):
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    idx = trade_dates.index(date)
    if idx + hold_days >= len(trade_dates):
        return pd.Series(dtype=float)
    future_date = trade_dates[idx + hold_days]
    current_close = panel.loc[date, "close"]
    future_close = panel.loc[future_date, "close"]
    common = current_close.index.intersection(future_close.index)
    ret = future_close[common] / current_close[common] - 1.0
    return ret


def _spearmanr(x, y):
    """手动 Spearman 秩相关（无需 scipy）。"""
    rx = x.rank().values
    ry = y.rank().values
    n = len(rx)
    if n < 10:
        return np.nan
    # Pearson on ranks
    rx_m, ry_m = rx.mean(), ry.mean()
    num = ((rx - rx_m) * (ry - ry_m)).sum()
    d1 = ((rx - rx_m) ** 2).sum()
    d2 = ((ry - ry_m) ** 2).sum()
    if d1 == 0 or d2 == 0:
        return np.nan
    return num / (np.sqrt(d1) * np.sqrt(d2))


def compute_rank_ic(factor_series, forward_ret):
    common = factor_series.dropna().index.intersection(forward_ret.dropna().index)
    if len(common) < 10:
        return np.nan
    f = factor_series[common]
    r = forward_ret[common]
    return _spearmanr(f, r)


def get_quantile_returns(factor_series, forward_ret, n_groups=5):
    """按因子值分 Q1~Q5，计算各组平均收益。"""
    common = factor_series.dropna().index.intersection(forward_ret.dropna().index)
    f = factor_series[common]
    r = forward_ret[common]
    if len(common) < 10:
        return {}
    # 用 rank 代替值本身，避免 qcut 重复边问题
    try:
        labels = [f"Q{i}" for i in range(1, n_groups + 1)]
        groups = pd.qcut(f.rank(), n_groups, labels=labels, duplicates="drop")
    except Exception:
        return {}
    result = {}
    for label in labels:
        mask = groups == label
        if mask.sum() > 0:
            result[label] = r[mask].mean()
    return result


def run_ic_test(panel, fin_ffill, basic_df):
    rebalance_dates = get_monthly_rebalance_dates(panel)
    print(f"[ic_test] 调仓日: {len(rebalance_dates)} 个")

    factor_names = list(FACTOR_CONFIG.keys())
    ic_records = []

    total = len(rebalance_dates)
    for i, date in enumerate(rebalance_dates):
        if (i + 1) % 20 == 0:
            print(f"[ic_test] 进度 {i + 1}/{total}")

        try:
            factors = compute_all_factors(panel, fin_ffill, date)
        except Exception as e:
            print(f"  [skip] {date}: {e}")
            continue

        forward_ret = calc_forward_return(panel, date, hold_days=20)
        if len(forward_ret.dropna()) < 10:
            continue

        record = {"date": date}
        for name in factor_names:
            fs = factors[name]
            fs = winsorize(fs, *WINSORIZE_PCT)
            fs = standardize(fs)
            ic = compute_rank_ic(fs, forward_ret)
            record[f"IC_{name}"] = ic

            qret = get_quantile_returns(fs, forward_ret)
            for q, v in qret.items():
                record[f"{name}_{q}"] = v

        ic_records.append(record)

    return pd.DataFrame(ic_records)


def generate_report(ic_df):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    factor_names = list(FACTOR_CONFIG.keys())

    ic_stats = []
    for name in factor_names:
        col = f"IC_{name}"
        ic_series = ic_df[col].dropna()
        if len(ic_series) == 0:
            continue
        ic_mean = ic_series.mean()
        ic_std = ic_series.std()
        icir = ic_mean / ic_std if ic_std > 0 else 0
        ic_positive = (ic_series > 0).mean()
        ic_stats.append({
            "因子": name,
            "类别": FACTOR_CONFIG[name]["category"],
            "IC均值": ic_mean,
            "IC标准差": ic_std,
            "ICIR": icir,
            "IC>0占比": ic_positive,
            "样本数": len(ic_series),
        })
    stats_df = pd.DataFrame(ic_stats).sort_values("ICIR", ascending=False)

    stats_df.to_csv(f"{OUTPUT_DIR}/ic_statistics.csv", index=False, encoding="utf-8-sig")
    ic_df.to_csv(f"{OUTPUT_DIR}/ic_series.csv", index=False, encoding="utf-8-sig")

    group_cols = ["date"] + [c for c in ic_df.columns if any(
        c.endswith(f"_{q}") for q in ["Q1","Q2","Q3","Q4","Q5"])]
    ic_df[group_cols].to_csv(f"{OUTPUT_DIR}/group_returns.csv",
                             index=False, encoding="utf-8-sig")

    print("\n" + "=" * 60)
    print("IC 测试摘要")
    print("=" * 60)
    print(f"{'因子':<16} {'类别':<6} {'IC均值':>8} {'ICIR':>8} {'IC>0%':>8} {'样本':>6}")
    print("-" * 60)
    for _, row in stats_df.iterrows():
        print(f"{row['因子']:<16} {row['类别']:<6} "
              f"{row['IC均值']:>8.4f} {row['ICIR']:>8.4f} "
              f"{row['IC>0占比']:>7.0%} {row['样本数']:>6.0f}")

    _generate_html_report(ic_df, stats_df, f"{OUTPUT_DIR}/ic_report.html")
    return stats_df


def _generate_html_report(ic_df, stats_df, html_path):
    from datetime import datetime
    ic_table = ic_df[["date"] + [c for c in ic_df.columns if c.startswith("IC_")]].copy()
    ic_table["date"] = pd.to_datetime(ic_table["date"]).dt.strftime("%Y-%m")

    stat_html = stats_df.to_html(index=False, float_format=lambda x: f"{x:.4f}")
    recent_ic = ic_table.tail(24).to_html(index=False, float_format=lambda x: f"{x:.4f}")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>多因子 IC 测试报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; font-size: 13px; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: right; }}
th {{ background: #f0f0f0; text-align: center; }}
h2 {{ color: #333; }}
</style>
</head>
<body>
<h1>多因子 IC 测试报告</h1>
<p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>选股范围: 中证500+中证1000 | 调仓频率: 月频 | 持有期: 20个交易日</p>

<h2>IC 统计汇总</h2>
{stat_html}

<h2>IC 判定参考</h2>
<ul>
<li><b>|IC| > 0.03</b>：因子有效</li>
<li><b>ICIR > 0.5</b>：因子稳定</li>
<li><b>IC>0占比 > 55%</b>：方向一致性好</li>
</ul>

<h2>最近 24 期 IC 值</h2>
{recent_ic}

</body>
</html>"""
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
