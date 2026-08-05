# coding=utf-8
"""多因子评分组合回测：月度调仓、等权持仓、绩效分析。"""

import numpy as np
import pandas as pd
from pathlib import Path

from research.multi_factor_ic.data_loader import get_rebalance_dates, load_universe, build_panel, get_universe_at_date
from research.multi_factor_ic.scoring import MultiFactorScorer
from research.multi_factor_ic.ic_test import calc_forward_return
from research.multi_factor_ic.config import OUTPUT_DIR


def _industry_cap(scores, industry_map, top_n, max_pct=0.25):
    """行业中性化选股：在评分排序基础上，确保单行业不超过 max_pct。"""
    sorted_stocks = scores.dropna().sort_values(ascending=False)
    selected = []
    industry_count = {}

    for code in sorted_stocks.index:
        ind = industry_map.get(code, "未知")
        current = industry_count.get(ind, 0)
        max_allowed = max(1, int(top_n * max_pct))
        if current >= max_allowed:
            continue
        selected.append(code)
        industry_count[ind] = current + 1
        if len(selected) >= top_n:
            break

    # 如果不够 top_n，从剩余中补足
    if len(selected) < top_n:
        for code in sorted_stocks.index:
            if code not in selected:
                selected.append(code)
                if len(selected) >= top_n:
                    break

    return selected


def _apply_tx_costs(returns, one_way_cost=0.002):
    """扣除交易成本。monthly full-turnover: 买卖各一次。"""
    return [r - 2.0 * one_way_cost for r in returns]


def backtest(panel, fin_ffill, top_n=20, hold=1,
             industry_map=None, max_industry_pct=0.25, freq="M",
             tx_cost=0.002, dynamic_universe=True, filter_func=None, weights=None,
             start_date=None, end_date=None):
    """运行调仓回测。

    Args:
        panel: 面板数据
        fin_ffill: 财务数据
        top_n: 每期持仓数量
        hold: 持有期数（1=持有1期，即一个调仓周期）
        industry_map: 行业映射，None=不做行业中性化
        max_industry_pct: 单行业最大占比
        freq: 调仓频率 "M"=月 "2M"=双月 "Q"=季度
        tx_cost: 单边交易成本(佣金+印花税+滑点)，默认0.2%
        dynamic_universe: 是否使用动态滚动universe（消除生存偏差）

    Returns:
        equity_df, trades_df, metrics
    """
    rebalance_dates = get_rebalance_dates(panel, freq=freq)
    scorer = MultiFactorScorer()

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    warmup = max(120, int(len(trade_dates) * 0.05))
    valid_start = trade_dates[warmup] if warmup < len(trade_dates) else trade_dates[0]

    # 区间截断（用于分行情区间验证，禁止未来数据泄漏）
    valid_start_ts = pd.Timestamp(valid_start)
    if start_date is not None:
        valid_start_ts = max(valid_start_ts, pd.Timestamp(start_date))
    if end_date is not None:
        rebalance_dates = [d for d in rebalance_dates if pd.Timestamp(d) <= pd.Timestamp(end_date)]
    rebalance_dates = [d for d in rebalance_dates if pd.Timestamp(d) >= valid_start_ts]
    print(f"[backtest] 回测区间: {rebalance_dates[0]} ~ {rebalance_dates[-1]}")
    print(f"[backtest] 调仓次数: {len(rebalance_dates)}")

    portfolio_value = 1.0
    equity_curve = []
    trades_records = []

    if dynamic_universe:
        print("[backtest] 使用动态滚动universe")

    for i, rebal_date in enumerate(rebalance_dates):
        try:
            scores = scorer.score(panel, fin_ffill, rebal_date, filter_func=filter_func, weights=weights)
        except Exception as e:
            print(f"  [skip] {rebal_date}: {e}")
            continue

        if dynamic_universe:
            universe_at_date = get_universe_at_date(panel, rebal_date)
            scores = scores[scores.index.isin(universe_at_date)]

        if len(scores.dropna()) < top_n:
            print(f"  [skip] {rebal_date}: 评分不足 {top_n}")
            continue

        if industry_map is not None:
            top_stocks = _industry_cap(scores, industry_map, top_n, max_industry_pct)
        else:
            top_stocks = scores.dropna().sort_values(ascending=False).head(top_n).index.tolist()

        # 次日收盘价成交：评分日在 rebal_date（月末收盘后），买入在次日开盘后收盘价
        rebal_idx = trade_dates.index(rebal_date)
        if rebal_idx + 1 >= len(trade_dates):
            continue
        entry_date = trade_dates[rebal_idx + 1]

        for h in range(hold):
            hold_idx = i + h
            if hold_idx >= len(rebalance_dates) - 1:
                break
            next_rebal = rebalance_dates[hold_idx + 1]
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

            portfolio_ret = np.mean(stock_returns)
            portfolio_ret -= 2.0 * tx_cost
            portfolio_value *= (1 + portfolio_ret)

            equity_curve.append({
                "date": exit_date,
                "portfolio_value": portfolio_value,
                "period_return": portfolio_ret,
                "n_stocks": len(held_stocks),
            })

            trades_records.append({
                "entry_date": entry_date,
                "exit_date": exit_date,
                "stocks": ";".join(held_stocks[:5]) + (f"...({len(held_stocks)})" if len(held_stocks) > 5 else ""),
                "n_stocks": len(held_stocks),
                "period_return": portfolio_ret,
            })

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades_records)

    # 计算绩效指标
    metrics = _calc_metrics(equity_df, trades_df, panel)

    return equity_df, trades_df, metrics


def _calc_metrics(equity_df, trades_df, panel):
    """计算绩效指标（基于实际日历时间年化，适配任意调仓频率）。"""
    metrics = {}

    if len(equity_df) == 0:
        return metrics

    total_return = equity_df["portfolio_value"].iloc[-1] - 1.0

    # 实际年数
    first_date = pd.Timestamp(equity_df["date"].iloc[0])
    last_date = pd.Timestamp(equity_df["date"].iloc[-1])
    years = (last_date - first_date).days / 365.25
    # 保守下限：按实际调仓周期数估算（每个周期=调仓频率），避免短区间年化虚高
    if len(equity_df) >= 1:
        min_years = len(equity_df) / 6.0  # 双月调仓=每年6次，最少按实际周期数估算年数
        years = max(years, min_years)
    years = max(years, 1 / 12)

    # 年化收益
    if len(equity_df) < 3:
        # 短区间样本不足：年化不可信，仅报累计收益并标注
        ann_return = total_return
        ann_note = " (样本不足{}期, 仅累计)".format(len(equity_df))
    else:
        ann_return = (1 + total_return) ** (1 / years) - 1
        ann_note = ""

    # 最大回撤
    cummax = equity_df["portfolio_value"].cummax()
    drawdown = equity_df["portfolio_value"] / cummax - 1
    max_dd = drawdown.min()

    # 每期收益率 → 年化夏普
    periods_per_year = len(equity_df) / years
    rf_per_period = 0.025 / periods_per_year  # 无风险利率2.5%年化
    excess_returns = equity_df["period_return"] - rf_per_period
    if excess_returns.std() > 0 and excess_returns.mean() != 0:
        sharpe = np.sqrt(periods_per_year) * excess_returns.mean() / excess_returns.std()
    else:
        sharpe = 0

    # 胜率
    win_rate = (equity_df["period_return"] > 0).mean()

    # 平均持仓数
    avg_hold = equity_df["n_stocks"].mean()

    metrics.update({
        "总收益": f"{total_return:.1%}",
        "年化收益": f"{ann_return:.1%}{ann_note}" if len(equity_df) < 3 else f"{ann_return:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率": f"{win_rate:.0%}",
        "调仓次数": len(equity_df),
    })

    return metrics


def run_backtest(panel, fin_ffill, top_n_list=None, tx_cost=0.002, dynamic_universe=True):
    """运行多组参数回测并汇总。"""
    if top_n_list is None:
        top_n_list = [10, 20, 30]

    all_results = []
    for top_n in top_n_list:
        print(f"\n--- TOP {top_n} 回测 ---")
        equity_df, trades_df, metrics = backtest(panel, fin_ffill, top_n=top_n,
                                                  tx_cost=tx_cost, dynamic_universe=dynamic_universe)

        summary = {"top_n": top_n}
        summary.update(metrics)
        all_results.append(summary)

        # 保存明细
        top_dir = f"{OUTPUT_DIR}/backtest_top{top_n}"
        Path(top_dir).mkdir(parents=True, exist_ok=True)
        equity_df.to_csv(f"{top_dir}/equity.csv", index=False, encoding="utf-8-sig")
        trades_df.to_csv(f"{top_dir}/trades.csv", index=False, encoding="utf-8-sig")

    # 汇总比较
    summary_df = pd.DataFrame(all_results)
    summary_df.to_csv(f"{OUTPUT_DIR}/backtest_summary.csv",
                      index=False, encoding="utf-8-sig")

    print("\n" + "=" * 60)
    print("多因子选股回测汇总")
    print("=" * 60)
    print(summary_df.to_string(index=False))

    # 生成 HTML 回测报告
    _generate_html_report(summary_df)
    return summary_df


def _generate_html_report(summary_df):
    """生成回测 HTML 报告。"""
    from datetime import datetime
    table = summary_df.to_html(index=False)

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>多因子选股回测报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: center; }}
th {{ background: #f0f0f0; }}
.positive {{ color: green; }}
.negative {{ color: red; }}
</style>
</head>
<body>
<h1>多因子选股回测报告</h1>
<p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>策略: BP(30%) + 反转(25%) + 低波(25%) + ROE(20%)</p>
<p>调仓频率: 月度 | 持仓方式: 等权</p>

<h2>多组参数对比</h2>
{table}

<h2>持仓明细</h2>
<p>详见各子目录:</p>
<ul>
{"".join(f'<li><a href="backtest_top{n}/trades.csv">TOP {n}</a></li>' for n in [10, 20, 30])}
</ul>
</body>
</html>"""
    with open(f"{OUTPUT_DIR}/backtest_report.html", "w", encoding="utf-8") as f:
        f.write(html)


def build_industry_map(basic_df):
    """从基础信息 DataFrame 构建 ts_code -> industry 映射。"""
    mapping = {}
    for _, row in basic_df.iterrows():
        ind = row.get("industry", "")
        if pd.isna(ind) or ind == "":
            ind = "未知"
        mapping[row["ts_code"]] = ind
    return mapping


def compare_industry_neutralize(panel, fin_ffill, basic_df, tx_cost=0.002, dynamic_universe=True):
    """对比行业中性化前后的回测效果。"""
    industry_map = build_industry_map(basic_df)

    results = []
    for top_n in [10, 20, 30]:
        for neutralize in [False, True]:
            label = f"TOP{top_n} {'+行业中性化' if neutralize else ''}"
            print(f"\n--- {label} ---")
            im = industry_map if neutralize else None
            eq, td, met = backtest(panel, fin_ffill, top_n=top_n,
                                   industry_map=im, max_industry_pct=0.25,
                                   tx_cost=tx_cost, dynamic_universe=dynamic_universe)
            met["参数"] = label
            results.append(met)

            out = f"{OUTPUT_DIR}/indneu_top{top_n}{'_neutralized' if neutralize else ''}"
            Path(out).mkdir(parents=True, exist_ok=True)
            eq.to_csv(f"{out}/equity.csv", index=False, encoding="utf-8-sig")
            td.to_csv(f"{out}/trades.csv", index=False, encoding="utf-8-sig")

    summary = pd.DataFrame(results)
    summary.to_csv(f"{OUTPUT_DIR}/industry_neutralize_compare.csv",
                   index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print("行业中性化 - 对比")
    print("=" * 70)
    print(summary.to_string(index=False))

    _gen_indneu_html(summary)
    return summary


def compare_frequencies(panel, fin_ffill, top_n=20, tx_cost=0.002, dynamic_universe=True):
    """对比不同调仓频率的回测效果。"""
    freqs = [("周频", "W"), ("双周", "2W"), ("月频", "M"), ("双月", "2M"), ("季度", "Q")]
    results = []
    for label, freq in freqs:
        print(f"\n--- {label} ---")
        try:
            eq, td, met = backtest(panel, fin_ffill, top_n=top_n, freq=freq,
                                    tx_cost=tx_cost, dynamic_universe=dynamic_universe)
            met["参数"] = label
            results.append(met)
        except Exception as e:
            print(f"  [err] {label}: {e}")

    summary = pd.DataFrame(results)
    summary.to_csv(f"{OUTPUT_DIR}/freq_comparison.csv",
                   index=False, encoding="utf-8-sig")

    print("\n" + "=" * 70)
    print("调仓频率对比 (TOP {})".format(top_n))
    print("=" * 70)
    print(summary.to_string(index=False))

    _gen_freq_html(summary)
    return summary


def _gen_freq_html(summary_df):
    from datetime import datetime
    table = summary_df.to_html(index=False)
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>调仓频率对比</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: center; }}
th {{ background: #f0f0f0; }}
</style>
</head>
<body>
<h1>调仓频率对比报告</h1>
<p>生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>策略: BP(30%)+反转(25%)+低波(25%)+ROE(20%) | TOP 20 等权</p>
{table}
</body>
</html>"""
    with open(f"{OUTPUT_DIR}/freq_comparison.html", "w", encoding="utf-8") as f:
        f.write(html)


def _gen_indneu_html(summary_df):
    from datetime import datetime
    table = summary_df.to_html(index=False)
    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>行业中性化对比</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; font-size: 14px; }}
th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: center; }}
th {{ background: #f0f0f0; }}
.better {{ color: green; font-weight: bold; }}
</style>
</head>
<body>
<h1>行业中性化对比报告</h1>
<p>生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<p>策略: BP(30%)+反转(25%)+低波(25%)+ROE(20%) | 月频调仓等权</p>
<p>行业中性化: 单行业 ≤ 25%</p>
{table}
</body>
</html>"""
    with open(f"{OUTPUT_DIR}/industry_neutralize_compare.html", "w", encoding="utf-8") as f:
        f.write(html)


def backtest_stop_loss(panel, fin_ffill, top_n=20, freq="2M",
                       tx_cost=0.002, dynamic_universe=True,
                       stop_loss=-0.12, filter_func=None, weights=None,
                       start_date=None, end_date=None, max_weight_per_stock=None,
                       save_details=False, basic_df=None):
    """带盘中止损+替换的调仓回测（v2 修正版）。

    设计要点：
      - 评分日 rebal_date 收盘后打分 → 次日收盘价买入（消除前视）
      - 止损判断: 使用 D-1 收盘价判断是否触发
      - 止损执行: 在 D 日收盘卖出 + 买入替代票
      - 现金结算: bucket_capital 直接跟踪每个仓位的现金价值
    """
    rebalance_dates = get_rebalance_dates(panel, freq=freq)
    scorer = MultiFactorScorer()
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    warmup = max(120, int(len(trade_dates) * 0.05))
    valid_start = trade_dates[warmup] if warmup < len(trade_dates) else trade_dates[0]
    # 区间截断（用于分行情区间验证，禁止未来数据泄漏）
    valid_start_ts = pd.Timestamp(valid_start)
    if start_date is not None:
        valid_start_ts = max(valid_start_ts, pd.Timestamp(start_date))
    if end_date is not None:
        rebalance_dates = [d for d in rebalance_dates if pd.Timestamp(d) <= pd.Timestamp(end_date)]
    rebalance_dates = [d for d in rebalance_dates if pd.Timestamp(d) >= valid_start_ts]
    print("[backtest_sl] 区间: {} ~ {}".format(rebalance_dates[0], rebalance_dates[-1]))
    print("[backtest_sl] 调仓: {}次, 止损线: {:.0%}".format(len(rebalance_dates), stop_loss))

    portfolio_value = 1.0
    equity_curve = []
    trades_records = []
    sl_events = []
    holdings_details = []  # For save_details=True

    if dynamic_universe:
        print("[backtest_sl] 动态滚动universe")

    for i, rebal_date in enumerate(rebalance_dates):
        if i >= len(rebalance_dates) - 1:
            break
        next_rebal = rebalance_dates[i + 1]

        try:
            scores = scorer.score(panel, fin_ffill, rebal_date, filter_func=filter_func, weights=weights)
        except Exception as e:
            print("  [skip] {}: {}".format(rebal_date, e))
            continue

        if dynamic_universe:
            universe_at_date = get_universe_at_date(panel, rebal_date)
            scores = scores[scores.index.isin(universe_at_date)]

        if len(scores.dropna()) < top_n:
            continue

        sorted_candidates = scores.dropna().sort_values(ascending=False).index.tolist()
        top_stocks = sorted_candidates[:top_n]

        # 次日收盘价成交: rebal_date+1 买入
        rebal_idx = trade_dates.index(rebal_date)
        if rebal_idx + 1 >= len(trade_dates):
            continue
        entry_date = trade_dates[rebal_idx + 1]

        exit_idx = trade_dates.index(next_rebal)
        if exit_idx + 1 >= len(trade_dates):
            continue
        exit_date = trade_dates[exit_idx + 1]

        # 持仓初始化: 等权分配资金（支持单票上限）
        bucket_capital = {}  # code -> 当前现金价值
        entry_prices = {}
        entry_close = panel.loc[entry_date, "open"]
        for code in top_stocks:
            cp = entry_close.get(code)
            if cp is not None and cp > 0 and not pd.isna(cp):
                bucket_capital[code] = 0.0  # 临时赋值
                entry_prices[code] = cp

        if len(bucket_capital) == 0:
            continue

        n = len(bucket_capital)
        if max_weight_per_stock is not None:
            # 单票上限：先按上限分配，剩余资金平分给未达上限的票（迭代至收敛）
            caps = {code: portfolio_value * max_weight_per_stock for code in bucket_capital}
            allocated = {code: 0.0 for code in bucket_capital}
            remaining = portfolio_value
            active = list(bucket_capital.keys())
            while remaining > 1e-6 and active:
                per = remaining / len(active)
                still_active = []
                for code in active:
                    give = min(per, caps[code] - allocated[code])
                    allocated[code] += give
                    remaining -= give
                    if allocated[code] < caps[code] - 1e-6:
                        still_active.append(code)
                active = still_active
            for code in bucket_capital:
                bucket_capital[code] = allocated[code]
        else:
            eq_capital = portfolio_value / n
            for code in bucket_capital:
                bucket_capital[code] = eq_capital

        # ===== save_details: 采集逐股持仓明细 (Phase 0补存) =====
        if save_details and basic_df is not None:
            ind_map = dict(zip(basic_df['ts_code'], basic_df['industry']))
            score_map = scores.to_dict()
            try:
                mv_map = panel.loc[rebal_date, 'circ_mv'].to_dict()
            except (KeyError, IndexError):
                mv_map = {}
            for code in bucket_capital:
                holdings_details.append({
                    'rebalance_date': rebal_date,
                    'stock_code': code,
                    'weight': bucket_capital[code] / portfolio_value,
                    'raw_score': score_map.get(code, pd.NA),
                    'industry': ind_map.get(code, '未知'),
                    'circ_mv': mv_map.get(code, pd.NA),
                })

        # 每日止损监控（从 entry_date+1 开始）
        period_dates = [d for d in trade_dates if entry_date < d <= exit_date]
        n_sl = 0

        for day in period_dates:
            # 使用 D-1 收盘判断
            prev_idx = trade_dates.index(day) - 1
            if prev_idx < 0:
                continue
            prev_close = panel.loc[trade_dates[prev_idx], "close"]

            sell_queue = []
            for code in list(entry_prices.keys()):
                ep = entry_prices[code]
                pc = prev_close.get(code)
                if pc is None or pc == 0 or pd.isna(pc):
                    continue
                ret = pc / ep - 1.0
                if ret <= stop_loss:
                    sell_queue.append(code)

            if not sell_queue:
                continue

            # 在 D 日开盘执行卖出+买入
            day_close = panel.loc[day, "open"]
            for code in sell_queue:
                ep = entry_prices[code]
                sp = day_close.get(code)
                if sp is None or sp == 0 or pd.isna(sp):
                    continue
                ret_actual = sp / ep - 1.0
                # 现金结算
                cash = bucket_capital[code] * sp / ep
                del bucket_capital[code]
                del entry_prices[code]
                n_sl += 1

                sl_events.append({
                    "rebal_date": rebal_date,
                    "exit_date": exit_date,
                    "sell_date": day,
                    "sold_code": code,
                    "entry_price": ep,
                    "sell_price": sp,
                    "loss_pct": ret_actual,
                })

                # 找替代票：最高评分不在持仓的（排除刚卖出的同票）
                replacement = None
                for cand in sorted_candidates:
                    if cand not in entry_prices and cand != code:
                        bp = day_close.get(cand)
                        if bp is not None and bp > 0 and not pd.isna(bp):
                            replacement = cand
                            break

                if replacement is not None:
                    bucket_capital[replacement] = cash
                    entry_prices[replacement] = bp

        # 期末结算
        total_value = 0.0
        n_final = 0
        held_codes = []
        exit_open = panel.loc[exit_date, "open"]
        for code in list(entry_prices.keys()):
            ep = entry_prices[code]
            ec = exit_open.get(code)
            if ec is not None and ec > 0 and not pd.isna(ec):
                total_value += bucket_capital[code] * ec / ep
                n_final += 1
                held_codes.append(code)

        if n_final == 0:
            continue

        period_ret = total_value / portfolio_value - 1.0
        # 交易成本: 初始买入(N次) + 期末卖出(N次) + 每笔止损(卖出+买入, n_sl组)
        total_trades = n + n + 2 * n_sl  # 初始买 + 期末卖 + 止损买卖
        period_ret -= total_trades * tx_cost / n

        portfolio_value = total_value  # 更新组合净值

        equity_curve.append({
            "date": exit_date,
            "portfolio_value": portfolio_value,
            "period_return": period_ret,
            "n_stocks": n_final,
            "n_sl_events": n_sl,
        })

        top5_str = ";".join(held_codes[:5]) + ("...({})".format(n_final) if n_final > 5 else "")
        trades_records.append({
            "entry_date": entry_date,
            "exit_date": exit_date,
            "stocks": top5_str,
            "n_stocks": n_final,
            "period_return": period_ret,
            "n_sl_events": n_sl,
        })

    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades_records)
    sl_events_df = pd.DataFrame(sl_events)

    metrics = _calc_metrics(equity_df, trades_df, panel)
    if len(sl_events_df) > 0:
        metrics["止损次数"] = str(len(sl_events_df))
        avg_sl = sl_events_df["loss_pct"].mean()
        metrics["平均止损幅度"] = "{:.1%}".format(avg_sl)

    # ===== save_details: 导出逐股持仓明细文件 (Phase 0补存) =====
    if save_details:
        if len(holdings_details) > 0:
            detail_path = Path(__file__).parent / "reports/v3_optimize/holdings_detail.csv"
            detail_path.parent.mkdir(parents=True, exist_ok=True)
            holdings_df = pd.DataFrame(holdings_details)
            holdings_df.to_csv(detail_path, index=False, encoding="utf-8-sig")
            print(f"[save_details] 已导出 {len(holdings_df)} 条持仓明细至: {detail_path.resolve()}")
        else:
            print("[save_details] 警告: 无持仓明细可导出，请检查basic_df参数是否正确传入")

    return equity_df, trades_df, sl_events_df, metrics


def compare_stop_loss(panel, fin_ffill, top_n=20, freq="2M",
                      tx_cost=0.002, dynamic_universe=True):
    """对比无止损 vs 带止损的双月回测效果。"""
    results = []

    print("\n--- 无止损 (双月) ---")
    eq, td, met = backtest(panel, fin_ffill, top_n=top_n, freq=freq,
                           tx_cost=tx_cost, dynamic_universe=dynamic_universe)
    met["参数"] = "无止损"
    met["止损次数"] = "0"
    results.append(met)

    for sl in [-0.08, -0.12, -0.16]:
        print(f"\n--- 止损 {sl:.0%} (双月) ---")
        eq, td, sl_df, met = backtest_stop_loss(panel, fin_ffill, top_n=top_n, freq=freq,
                                                 tx_cost=tx_cost,
                                                 dynamic_universe=dynamic_universe,
                                                 stop_loss=sl)
        met["参数"] = "止损{:.0%}".format(sl)
        results.append(met)

        sl_path = "{}/stop_loss/sl_{}pct".format(OUTPUT_DIR, abs(int(sl * 100)))
        Path(sl_path).mkdir(parents=True, exist_ok=True)
        eq.to_csv(sl_path + "/equity.csv", index=False, encoding="utf-8-sig")
        td.to_csv(sl_path + "/trades.csv", index=False, encoding="utf-8-sig")
        if len(sl_df) > 0:
            sl_df.to_csv(sl_path + "/sl_events.csv", index=False, encoding="utf-8-sig")

    summary = pd.DataFrame(results)
    summary.to_csv("{}/stop_loss_comparison.csv".format(OUTPUT_DIR),
                   index=False, encoding="utf-8-sig")

    print("\n" + "=" * 80)
    print("止损效果对比 (双月 TOP{})".format(top_n))
    print("=" * 80)
    print(summary.to_string(index=False))

    return summary
