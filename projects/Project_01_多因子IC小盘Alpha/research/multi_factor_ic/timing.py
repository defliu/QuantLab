# coding=utf-8
"""大盘择时增强：基于选股池等权指数的 MA 均线系统。"""

import numpy as np
import pandas as pd

from research.multi_factor_ic.data_loader import load_universe, build_panel
from research.multi_factor_ic.scoring import MultiFactorScorer
from research.multi_factor_ic.backtest import backtest
from research.multi_factor_ic.config import OUTPUT_DIR
from pathlib import Path


def build_market_index(panel):
    """构建选股池等权市场指数。

    每日对池内所有股票取 close 的等权平均 → 合成指数。
    返回 Series(date -> index_value)
    """
    # 每日等权平均收盘价
    daily_close = panel["close"].groupby("trade_date").mean()
    daily_close = daily_close.sort_index()
    # 归一化到 1000 点
    first = daily_close.iloc[0]
    index_series = daily_close / first * 1000
    return index_series


def market_timing_signal(market_index, fast_ma=20, slow_ma=60, smooth=3):
    """大盘择时信号。

    Args:
        market_index: Series(date -> index_value)
        fast_ma: 快线窗口
        slow_ma: 慢线窗口
        smooth: 连续确认天数（过滤假信号）

    Returns:
        Series(date -> position: 0.0 ~ 1.0)
    """
    ma_fast = market_index.rolling(fast_ma).mean()
    ma_slow = market_index.rolling(slow_ma).mean()

    # 原始信号: 快线 > 慢线 = 1, 否则 = 0
    raw = (ma_fast > ma_slow).astype(float)

    # 平滑: 连续确认 (smooth-of-N)
    smooth_signal = raw.rolling(smooth).min().fillna(0)
    return smooth_signal


def backtest_with_timing(panel, fin_ffill, top_n=20, hold_months=1,
                         fast_ma=20, slow_ma=60, smooth=3):
    """带大盘择时的回测。"""
    # 构建市场指数
    market_index = build_market_index(panel)
    timing = market_timing_signal(market_index, fast_ma, slow_ma, smooth)

    # 先跑基础回测拿调仓日
    from research.multi_factor_ic.data_loader import get_monthly_rebalance_dates
    rebalance_dates = get_monthly_rebalance_dates(panel)
    scorer = MultiFactorScorer()

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    warmup = max(120, int(len(trade_dates) * 0.05))
    valid_start = trade_dates[warmup] if warmup < len(trade_dates) else trade_dates[0]
    rebalance_dates = [d for d in rebalance_dates if d >= valid_start]

    print(f"[timing] 回测区间: {rebalance_dates[0]} ~ {rebalance_dates[-1]}")
    print(f"[timing] 调仓次数: {len(rebalance_dates)}")

    portfolio_value = 1.0
    equity_curve = []
    trades_records = []

    for i, rebal_date in enumerate(rebalance_dates):
        # 获取调仓日的择时信号
        pos = timing.get(rebal_date, 0)

        # 评分选股
        try:
            scores = scorer.score(panel, fin_ffill, rebal_date)
        except Exception:
            equity_curve.append({
                "date": rebal_date, "portfolio_value": portfolio_value,
                "period_return": 0, "n_stocks": 0, "position": pos,
            })
            continue

        if len(scores.dropna()) < top_n:
            continue

        top_stocks = scores.dropna().sort_values(ascending=False).head(top_n).index.tolist()

        # 计算持有期收益（仅 pos 仓位）
        for months in range(hold_months):
            hold_idx = i + months
            if hold_idx >= len(rebalance_dates) - 1:
                break
            exit_date = rebalance_dates[hold_idx + 1]

            stock_rets = []
            held = []
            for code in top_stocks:
                ec = panel.loc[rebal_date, "close"].get(code)
                xc = panel.loc[exit_date, "close"].get(code)
                if ec is not None and xc is not None and ec > 0 and not pd.isna(ec):
                    stock_rets.append(xc / ec - 1.0)
                    held.append(code)

            if len(stock_rets) == 0:
                continue

            # 等权组合原始收益
            raw_ret = np.mean(stock_rets)
            # 择时调整后的收益
            timed_ret = raw_ret * pos  # pos=1 全仓, pos=0 空仓

            portfolio_value *= (1 + timed_ret)

            equity_curve.append({
                "date": exit_date,
                "portfolio_value": portfolio_value,
                "period_return": timed_ret,
                "n_stocks": len(held) if pos > 0 else 0,
                "position": pos,
            })

            trades_records.append({
                "entry_date": rebal_date,
                "exit_date": exit_date,
                "n_stocks": len(held),
                "position": pos,
                "raw_return": raw_ret,
                "timed_return": timed_ret,
            })

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades_records)

    # 绩效指标
    metrics = _calc_metrics(equity_df)

    return equity_df, trades_df, metrics


def _calc_metrics(equity_df):
    metrics = {}
    if len(equity_df) == 0:
        return metrics
    total_return = equity_df["portfolio_value"].iloc[-1] - 1.0
    n_periods = len(equity_df)
    years = n_periods / 12
    ann_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    cummax = equity_df["portfolio_value"].cummax()
    drawdown = equity_df["portfolio_value"] / cummax - 1
    max_dd = drawdown.min()
    rf = 0.025 / 12
    excess = equity_df["period_return"] - rf
    sharpe = np.sqrt(12) * excess.mean() / excess.std() if excess.std() > 0 else 0
    win_rate = (equity_df["period_return"] > 0).mean()

    # 平均仓位
    avg_pos = equity_df["position"].mean() if "position" in equity_df.columns else 1.0

    metrics.update({
        "总收益": f"{total_return:.1%}",
        "年化收益": f"{ann_return:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率": f"{win_rate:.0%}",
        "平均仓位": f"{avg_pos:.0%}",
        "调仓次数": len(equity_df),
    })
    return metrics


def compare_timing_params(panel, fin_ffill, param_grid=None):
    """多参数网格搜索。"""
    if param_grid is None:
        param_grid = [
            {"fast_ma": 10, "slow_ma": 30, "smooth": 2, "label": "MA10/30"},
            {"fast_ma": 20, "slow_ma": 60, "smooth": 3, "label": "MA20/60"},
            {"fast_ma": 30, "slow_ma": 120, "smooth": 5, "label": "MA30/120"},
        ]

    results = []
    # 基础回测（无择时）
    print("\n--- 无择时 (基准) ---")
    eq, td, met = backtest(panel, fin_ffill, top_n=20)
    met["参数"] = "无择时"
    results.append(met)

    for p in param_grid:
        print(f"\n--- 择时 {p['label']} ---")
        eq, td, met = backtest_with_timing(
            panel, fin_ffill, top_n=20,
            fast_ma=p["fast_ma"], slow_ma=p["slow_ma"], smooth=p["smooth"],
        )
        met["参数"] = p["label"]
        results.append(met)

        # 保存明细
        out_dir = f"{OUTPUT_DIR}/timing_{p['label'].replace('/', '_')}"
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        eq.to_csv(f"{out_dir}/equity.csv", index=False, encoding="utf-8-sig")
        td.to_csv(f"{out_dir}/trades.csv", index=False, encoding="utf-8-sig")

    summary_df = pd.DataFrame(results)
    summary_df.to_csv(f"{OUTPUT_DIR}/timing_comparison.csv",
                      index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print("大盘择时增强 - 对比")
    print("=" * 70)
    print(summary_df.to_string(index=False))

    _gen_html(summary_df)
    return summary_df


def _gen_html(summary_df):
    from datetime import datetime
    table = summary_df.to_html(index=False)
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>大盘择时增强对比</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: center; }}
th {{ background: #f0f0f0; }}
</style>
</head>
<body>
<h1>大盘择时增强 - 对比报告</h1>
<p>生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>策略: BP(30%)+反转(25%)+低波(25%)+ROE(20%) | TOP 20 等权月频调仓</p>
<p>择时信号: 选股池等权指数(快MA vs 慢MA) + 连续确认</p>
{table}
</body>
</html>"""
    with open(f"{OUTPUT_DIR}/timing_comparison.html", "w", encoding="utf-8") as f:
        f.write(html)
