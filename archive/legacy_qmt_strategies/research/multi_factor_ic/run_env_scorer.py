# coding=utf-8
"""env_scorer 移植到多因子IC策略 — 择时增强对比

对比三种模式:
  1. Baseline: 无择时 (原始多因子IC)
  2. MA Timing: MA20/MA60 二值择时 (binary)
  3. env_scorer: 复合环境评分连续择时 (4-tier)

最优参数: 0-30亿 + 双月 + TOP80 + 止损-12% + 成交额>2000万
"""
import sys, os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(os.path.dirname(_THIS_DIR))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

import numpy as np
import pandas as pd
from pathlib import Path
from research.multi_factor_ic.data_loader import (
    load_universe, build_panel, get_rebalance_dates, get_universe_at_date
)
from research.multi_factor_ic.scoring import MultiFactorScorer
from research.multi_factor_ic.config import OUTPUT_DIR
from backtest.strategies.research.market_environment.scorer import CompositeEnvironmentScorer

# ---- 最优参数 (from MEMORY) ----
TOP_N = 80
STOP_LOSS = -0.12
FREQ = "2M"
TX_COST = 0.0015  # 0.15% 单边

# ---- 环境评分参数 ----
POSITION_MAP = [
    (0.3,  1.0, 1.0),
    (0.0,  0.7, 0.7),
    (-0.3, 0.4, 0.4),
    (-1.0, 0.0, 0.0),
]

def _env_to_position(composite):
    """composite score -> position ratio (0.0 ~ 1.0)."""
    for threshold, pos_ratio, _ in POSITION_MAP:
        if composite > threshold:
            return pos_ratio
    return 0.0


def build_benchmark_from_panel(panel):
    """从 panel 构建合成 benchmark (等权平均 close).

    Returns: dict {date_str: close_value}
    """
    close_wide = panel["close"].unstack("ts_code")
    daily_avg = close_wide.mean(axis=1).dropna().sort_index()
    first = daily_avg.iloc[0]
    benchmark = daily_avg / first
    return {str(d): float(v) for d, v in benchmark.items()}


def backtest_env_scorer(panel, fin_ffill, top_n=TOP_N, freq=FREQ,
                        tx_cost=TX_COST, stop_loss=STOP_LOSS,
                        env_weights=None):
    """带 env_scorer 择时的回测。

    env_scorer 在每个调仓日计算复合环境评分，映射为仓位系数。
    """
    rebalance_dates = get_rebalance_dates(panel, freq=freq)
    scorer = MultiFactorScorer()
    env_scorer = CompositeEnvironmentScorer(weights=env_weights)

    # 构建 benchmark
    benchmark_closes = build_benchmark_from_panel(panel)
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())

    warmup = max(120, int(len(trade_dates) * 0.05))
    valid_start = trade_dates[warmup] if warmup < len(trade_dates) else trade_dates[0]
    rebalance_dates = [d for d in rebalance_dates if d >= valid_start]

    print(f"[env_scorer] 回测区间: {rebalance_dates[0]} ~ {rebalance_dates[-1]}")
    print(f"[env_scorer] 调仓次数: {len(rebalance_dates)}")

    portfolio_value = 1.0
    cash_ratio = 1.0  # 现金比例
    equity_curve = []
    trades_records = []
    flatten_count = 0

    # 止损跟踪
    holdings = {}  # code -> (entry_price, entry_date, pos_weight)

    for i, rebal_date in enumerate(rebalance_dates):
        # ---- env_scorer 计算 ----
        try:
            # 构建当前日期前的 benchmark_closes 切片
            bc_slice = {k: v for k, v in benchmark_closes.items()
                        if pd.Timestamp(k) <= pd.Timestamp(rebal_date)}
            # 用 panel 的当前日期 market_window 构建 breadth 信号需要的数据
            # 简化：只用 trend/volatility/volume 三个信号 (breadth 需要逐只股票MA20)
            env_result = env_scorer.score(
                {"benchmark_closes": bc_slice}, str(rebal_date), market_window=None
            )
            composite = env_result["composite"]
            pos_ratio = _env_to_position(composite)
        except Exception as e:
            print(f"  [env_scorer] {rebal_date}: {e}, fallback pos=1.0")
            composite = 0.5
            pos_ratio = 1.0

        if pos_ratio <= 0.0:
            flatten_count += 1
            # 清仓
            for code in list(holdings.keys()):
                del holdings[code]
            equity_curve.append({
                "date": rebal_date, "portfolio_value": portfolio_value,
                "period_return": 0, "n_stocks": 0,
                "composite": composite, "pos_ratio": pos_ratio,
            })
            continue

        # ---- 评分选股 ----
        try:
            scores = scorer.score(panel, fin_ffill, rebal_date)
        except Exception as e:
            print(f"  [skip] {rebal_date}: {e}")
            continue

        # 动态 universe
        universe_at_date = get_universe_at_date(panel, rebal_date)
        scores = scores[scores.index.isin(universe_at_date)]

        if len(scores.dropna()) < top_n:
            print(f"  [skip] {rebal_date}: 评分不足 {top_n}")
            continue

        top_stocks = scores.dropna().sort_values(ascending=False).head(top_n).index.tolist()

        # ---- 止损检查 (D-1 收盘判断, D 日执行) ----
        if i > 0 and stop_loss is not None:
            prev_date = rebalance_dates[i - 1]
            for code in list(holdings.keys()):
                entry_price, _, _ = holdings[code]
                # 用前一日 close 判断
                try:
                    prev_close = panel.loc[prev_date, "close"].get(code)
                    if prev_close is not None and not pd.isna(prev_close) and entry_price > 0:
                        pnl = prev_close / entry_price - 1.0
                        if pnl <= stop_loss:
                            # 止损卖出
                            del holdings[code]
                except Exception:
                    pass

        # ---- 持有期收益 ----
        rebal_idx = trade_dates.index(rebal_date)
        if rebal_idx + 1 >= len(trade_dates):
            continue
        entry_date = trade_dates[rebal_idx + 1]

        next_rebal_idx = min(i + 1, len(rebalance_dates) - 1)
        if next_rebal_idx >= len(rebalance_dates):
            continue
        next_rebal = rebalance_dates[next_rebal_idx]
        exit_idx = trade_dates.index(next_rebal)
        if exit_idx + 1 >= len(trade_dates):
            continue
        exit_date = trade_dates[exit_idx + 1]

        stock_returns = []
        held_stocks = []
        for code in top_stocks:
            entry_close = panel.loc[entry_date, "open"].get(code)
            exit_close = panel.loc[exit_date, "open"].get(code)
            if entry_close is None or exit_close is None or entry_close == 0:
                continue
            if pd.isna(entry_close) or pd.isna(exit_close):
                continue
            ret = exit_close / entry_close - 1.0
            stock_returns.append(ret)
            held_stocks.append(code)

        if len(stock_returns) == 0:
            continue

        # 等权组合收益
        raw_ret = np.mean(stock_returns)
        # 环境择时调整: 只有 pos_ratio 部分的仓位参与
        timed_ret = raw_ret * pos_ratio
        # 扣交易成本 (仅对变化的仓位)
        turnover_cost = 2.0 * tx_cost * pos_ratio  # 近似
        portfolio_ret = timed_ret - turnover_cost

        portfolio_value *= (1 + portfolio_ret)

        equity_curve.append({
            "date": exit_date, "portfolio_value": portfolio_value,
            "period_return": portfolio_ret, "n_stocks": len(held_stocks),
            "composite": composite, "pos_ratio": pos_ratio,
            "raw_return": raw_ret,
        })

        trades_records.append({
            "entry_date": entry_date, "exit_date": exit_date,
            "n_stocks": len(held_stocks),
            "composite": composite, "pos_ratio": pos_ratio,
            "raw_return": raw_ret, "timed_return": portfolio_ret,
        })

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades_records)
    metrics = _calc_metrics(equity_df)
    metrics["清仓次数"] = flatten_count
    return equity_df, trades_df, metrics


def backtest_ma_timing(panel, fin_ffill, top_n=TOP_N, freq=FREQ,
                       tx_cost=TX_COST, fast_ma=20, slow_ma=60):
    """MA 择时回测 (binary: 1.0 or 0.0)."""
    rebalance_dates = get_rebalance_dates(panel, freq=freq)
    scorer = MultiFactorScorer()

    # 构建市场指数 (pivot to wide then mean)
    close_wide = panel["close"].unstack("ts_code")
    daily_avg = close_wide.mean(axis=1).dropna().sort_index()
    market_index = daily_avg / daily_avg.iloc[0] * 1000

    ma_fast = market_index.rolling(fast_ma).mean()
    ma_slow = market_index.rolling(slow_ma).mean()
    timing = (ma_fast > ma_slow).astype(float)

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    warmup = max(120, int(len(trade_dates) * 0.05))
    valid_start = trade_dates[warmup] if warmup < len(trade_dates) else trade_dates[0]
    rebalance_dates = [d for d in rebalance_dates if d >= valid_start]

    print(f"[MA_timing] 回测区间: {rebalance_dates[0]} ~ {rebalance_dates[-1]}")
    print(f"[MA_timing] 调仓次数: {len(rebalance_dates)}")

    portfolio_value = 1.0
    equity_curve = []
    trades_records = []
    out_count = 0

    for i, rebal_date in enumerate(rebalance_dates):
        pos = timing.get(rebal_date, 0)

        try:
            scores = scorer.score(panel, fin_ffill, rebal_date)
        except Exception:
            continue

        universe_at_date = get_universe_at_date(panel, rebal_date)
        scores = scores[scores.index.isin(universe_at_date)]

        if len(scores.dropna()) < top_n:
            continue

        top_stocks = scores.dropna().sort_values(ascending=False).head(top_n).index.tolist()

        if pos <= 0:
            out_count += 1
            equity_curve.append({
                "date": rebal_date, "portfolio_value": portfolio_value,
                "period_return": 0, "n_stocks": 0, "position": pos,
            })
            continue

        rebal_idx = trade_dates.index(rebal_date)
        if rebal_idx + 1 >= len(trade_dates):
            continue
        entry_date = trade_dates[rebal_idx + 1]

        next_rebal_idx = min(i + 1, len(rebalance_dates) - 1)
        next_rebal = rebalance_dates[next_rebal_idx]
        exit_idx = trade_dates.index(next_rebal)
        if exit_idx + 1 >= len(trade_dates):
            continue
        exit_date = trade_dates[exit_idx + 1]

        stock_returns = []
        for code in top_stocks:
            ec = panel.loc[entry_date, "open"].get(code)
            xc = panel.loc[exit_date, "open"].get(code)
            if ec is not None and xc is not None and ec > 0 and not pd.isna(ec):
                stock_returns.append(xc / ec - 1.0)

        if len(stock_returns) == 0:
            continue

        raw_ret = np.mean(stock_returns)
        portfolio_ret = raw_ret - 2.0 * tx_cost
        portfolio_value *= (1 + portfolio_ret)

        equity_curve.append({
            "date": exit_date, "portfolio_value": portfolio_value,
            "period_return": portfolio_ret, "n_stocks": len(stock_returns),
            "position": pos, "raw_return": raw_ret,
        })

        trades_records.append({
            "entry_date": entry_date, "exit_date": exit_date,
            "n_stocks": len(stock_returns), "position": pos,
            "raw_return": raw_ret,
        })

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades_records)
    metrics = _calc_metrics(equity_df)
    metrics["空仓次数"] = out_count
    return equity_df, trades_df, metrics


def backtest_baseline(panel, fin_ffill, top_n=TOP_N, freq=FREQ, tx_cost=TX_COST):
    """无择时基线回测。"""
    from research.multi_factor_ic.backtest import backtest
    equity_df, trades_df, metrics = backtest(
        panel, fin_ffill, top_n=top_n, freq=freq, tx_cost=tx_cost
    )
    return equity_df, trades_df, metrics


def _calc_metrics(equity_df):
    """计算绩效指标。"""
    metrics = {}
    if len(equity_df) == 0:
        return metrics
    total_return = equity_df["portfolio_value"].iloc[-1] - 1.0
    n_periods = len(equity_df)
    first_date = pd.Timestamp(equity_df["date"].iloc[0])
    last_date = pd.Timestamp(equity_df["date"].iloc[-1])
    years = (last_date - first_date).days / 365.25
    min_years = n_periods / 6.0  # 双月=6次/年
    years = max(years, min_years, 1/12)

    ann_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
    cummax = equity_df["portfolio_value"].cummax()
    drawdown = equity_df["portfolio_value"] / cummax - 1
    max_dd = drawdown.min()
    rf = 0.025 / 12
    excess = equity_df["period_return"] - rf
    sharpe = np.sqrt(12) * excess.mean() / excess.std() if excess.std() > 0 else 0
    win_rate = (equity_df["period_return"] > 0).mean()

    metrics.update({
        "总收益": f"{total_return:.1%}",
        "年化收益": f"{ann_return:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率": f"{win_rate:.0%}",
        "调仓次数": n_periods,
    })
    return metrics


def main():
    print("=== env_scorer 移植到多因子IC策略 ===\n")

    # 1. 加载数据
    print("加载数据...")
    codes = load_universe()
    print(f"  候选池: {len(codes)} 只")

    panel, fin_ffill = build_panel(codes)
    print(f"  面板: {panel.shape}")

    # 2. Baseline (无择时)
    print("\n" + "=" * 60)
    print("1. Baseline (无择时)")
    print("=" * 60)
    eq_base, td_base, met_base = backtest_baseline(panel, fin_ffill)
    for k, v in met_base.items():
        print(f"  {k}: {v}")

    # 3. MA Timing
    print("\n" + "=" * 60)
    print("2. MA Timing (MA20/60 二值)")
    print("=" * 60)
    eq_ma, td_ma, met_ma = backtest_ma_timing(panel, fin_ffill)
    for k, v in met_ma.items():
        print(f"  {k}: {v}")

    # 4. env_scorer
    print("\n" + "=" * 60)
    print("3. env_scorer (复合环境评分)")
    print("=" * 60)
    eq_env, td_env, met_env = backtest_env_scorer(panel, fin_ffill)
    for k, v in met_env.items():
        print(f"  {k}: {v}")

    # 5. 对比
    print("\n" + "=" * 60)
    print("对比汇总")
    print("=" * 60)
    summary = pd.DataFrame([met_base, met_ma, met_env],
                           index=["Baseline", "MA_Timing", "env_scorer"])
    print(summary.to_string())

    # 6. 保存
    out_dir = Path(OUTPUT_DIR) / "env_scorer_comparison"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_dir / "summary.csv", encoding="utf-8-sig")
    eq_base.to_csv(out_dir / "baseline_equity.csv", index=False, encoding="utf-8-sig")
    eq_ma.to_csv(out_dir / "ma_equity.csv", index=False, encoding="utf-8-sig")
    eq_env.to_csv(out_dir / "env_equity.csv", index=False, encoding="utf-8-sig")
    print(f"\n结果已保存到: {out_dir}")


if __name__ == "__main__":
    main()
