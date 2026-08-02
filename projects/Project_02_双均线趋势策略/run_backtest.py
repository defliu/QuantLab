# coding=utf-8
"""双均线策略回测脚本（自包含，无外部项目依赖）"""

import os
import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# 数据路径（astock 第一数据源）
DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
START_DATE = "2018-01-01"
END_DATE = "2026-06-30"

# 导入策略（本目录 strategy/ 包，脚本直跑时 sys.path[0]=本目录）
from strategy.dual_ma import get_candidates, should_sell, STOP_LOSS, precompute_ma


def load_universe():
    """按最新交易日流通市值取前 4000 只（与原 data_loader 行为一致，避免全A行数爆炸）"""
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    latest_date = idx.get_level_values("trade_date").max()
    latest = daily.loc[idx.get_level_values("trade_date") == latest_date].copy()
    latest["circ_mv"] = latest["circ_mv"].fillna(0)
    selected = latest["circ_mv"].sort_values(ascending=False).index[:4000]
    return set(selected.get_level_values("ts_code"))


def build_panel(codes):
    """构建 (trade_date, ts_code) 面板，去 ST/停牌；双均线回测只用 close"""
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    daily = daily.loc[idx.get_level_values("ts_code").isin(codes)].copy()
    idx = daily.index
    trade_dates = idx.get_level_values("trade_date")
    start_ts = pd.Timestamp(START_DATE).date()
    end_ts = pd.Timestamp(END_DATE).date()
    daily = daily.loc[(trade_dates >= start_ts) & (trade_dates <= end_ts)].copy()

    idx = daily.index
    panel = pd.DataFrame({"close": daily["close"].values}, index=idx)
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]
    return panel, None


# 回测参数
INITIAL_CAPITAL = 100000
MAX_POSITIONS = 50
COMMISSION = 0.0003  # 佣金万三
STAMP_TAX = 0.001  # 印花税千一
SLIPPAGE = 0.001  # 滑点千一

OUTPUT_DIR = "E:/QuantLab/projects/Project_02_双均线趋势策略/reports"


def run_backtest(start_date="2020-01-01", end_date="2026-06-30"):
    """运行双均线策略回测"""
    print("=" * 60)
    print("双均线策略回测")
    print(f"策略: SMA 10/30 + 全A + {STOP_LOSS:.0%}止损")
    print(f"回测区间: {start_date} ~ {end_date}")
    print("=" * 60)
    
    # 加载数据
    print("\n[1/4] 加载数据...")
    codes = load_universe()
    panel, fin_ffill = build_panel(codes)
    
    # 预计算均线信号
    precompute_ma(panel)
    
    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    
    # 过滤日期范围
    start_ts = pd.Timestamp(start_date).date()
    end_ts = pd.Timestamp(end_date).date()
    trade_dates = [d for d in trade_dates if start_ts <= d <= end_ts]
    
    # 预热期
    warmup = 60
    trade_dates = trade_dates[warmup:]
    
    print(f"  股票数: {len(codes)}")
    print(f"  交易日: {len(trade_dates)}")
    
    # 初始化
    print("\n[2/4] 执行回测...")
    capital = INITIAL_CAPITAL
    positions = {}  # code -> {shares, entry_price, entry_date}
    equity_curve = []
    trades = []
    stop_loss_events = []
    
    for i, date in enumerate(trade_dates):
        if i % 100 == 0:
            print(f"  进度: {i}/{len(trade_dates)} ({date})")
        
        # ====== 止损和死叉检查 ======
        codes_to_sell = []
        for code, pos in list(positions.items()):
            should, reason = should_sell(panel, date, code, pos["entry_price"])
            if should:
                try:
                    sell_price = panel.loc[date, "close"].get(code)
                    if sell_price is not None and not pd.isna(sell_price):
                        codes_to_sell.append((code, reason, sell_price))
                except Exception:
                    pass
        
        # 执行卖出
        for code, reason, sell_price in codes_to_sell:
            if code not in positions:
                continue
            pos = positions[code]
            sell_amount = pos["shares"] * sell_price
            sell_cost = sell_amount * (COMMISSION + STAMP_TAX + SLIPPAGE)
            capital += sell_amount - sell_cost
            
            pnl = (sell_price / pos["entry_price"] - 1.0) * 100
            trades.append({
                "date": date,
                "code": code,
                "action": "卖出",
                "reason": reason,
                "price": sell_price,
                "shares": pos["shares"],
                "pnl_pct": pnl,
            })
            
            if reason == "止损":
                stop_loss_events.append({
                    "date": date,
                    "code": code,
                    "entry_price": pos["entry_price"],
                    "exit_price": sell_price,
                    "loss_pct": pnl,
                })
            
            del positions[code]
        
        # ====== 买入 ======
        candidates = get_candidates(panel, date, MAX_POSITIONS - len(positions))
        n_buy = min(MAX_POSITIONS - len(positions), len(candidates))
        
        for code in candidates[:n_buy]:
            if code in positions:
                continue
            
            try:
                buy_price = panel.loc[date, "close"].get(code)
                if buy_price is None or pd.isna(buy_price) or buy_price <= 0:
                    continue
            except Exception:
                continue
            
            # 等权分配
            alloc = capital / max(1, n_buy - candidates.index(code) + len(positions))
            alloc = min(alloc, capital * 0.95)
            
            if alloc < 1000:
                continue
            
            shares = int(alloc / buy_price / 100) * 100
            if shares < 100:
                continue
            
            buy_amount = shares * buy_price
            buy_cost = buy_amount * (COMMISSION + SLIPPAGE)
            capital -= buy_amount + buy_cost
            
            positions[code] = {
                "shares": shares,
                "entry_price": buy_price,
                "entry_date": date,
            }
            
            trades.append({
                "date": date,
                "code": code,
                "action": "买入",
                "reason": "金叉" if len(trades) == 0 else "趋势",
                "price": buy_price,
                "shares": shares,
                "pnl_pct": 0,
            })
        
        # ====== 记录净值 ======
        portfolio_value = capital
        for code, pos in positions.items():
            try:
                current_price = panel.loc[date, "close"].get(code)
                if current_price is not None and not pd.isna(current_price):
                    portfolio_value += pos["shares"] * current_price
            except Exception:
                pass
        
        equity_curve.append({
            "date": date,
            "portfolio_value": portfolio_value,
            "capital": capital,
            "n_positions": len(positions),
        })
    
    # 转换为DataFrame
    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades)
    sl_df = pd.DataFrame(stop_loss_events)
    
    print(f"\n  回测完成: {len(equity_df)} 个交易日, {len(trades)} 笔交易")
    
    # 计算指标
    print("\n[3/4] 计算绩效指标...")
    metrics = calc_metrics(equity_df, trades_df, sl_df)
    
    # 输出结果
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    for k, v in metrics.items():
        print(f"  {k}: {v}")
    
    # 生成报告
    print("\n[4/4] 生成报告...")
    report_path = generate_report(equity_df, trades_df, sl_df, metrics)
    
    print("\n" + "=" * 60)
    print("完成!")
    print(f"报告: {report_path}")
    print("=" * 60)
    
    return equity_df, trades_df, metrics


def calc_metrics(equity_df, trades_df, sl_df):
    """计算绩效指标"""
    metrics = {}
    
    if len(equity_df) == 0:
        return metrics
    
    total_return = equity_df["portfolio_value"].iloc[-1] / INITIAL_CAPITAL - 1.0
    
    first_date = pd.Timestamp(equity_df["date"].iloc[0])
    last_date = pd.Timestamp(equity_df["date"].iloc[-1])
    years = (last_date - first_date).days / 365.25
    years = max(years, 1/12)
    ann_return = (1 + total_return) ** (1 / years) - 1
    
    cummax = equity_df["portfolio_value"].cummax()
    drawdown = equity_df["portfolio_value"] / cummax - 1
    max_dd = drawdown.min()
    
    equity_df["daily_return"] = equity_df["portfolio_value"].pct_change()
    daily_returns = equity_df["daily_return"].dropna()
    rf_daily = 0.025 / 252
    excess_returns = daily_returns - rf_daily
    sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std() if excess_returns.std() > 0 else 0
    
    sell_trades = trades_df[trades_df["action"] == "卖出"]
    win_rate = len(sell_trades[sell_trades["pnl_pct"] > 0]) / len(sell_trades) if len(sell_trades) > 0 else 0
    
    metrics = {
        "总收益": f"{total_return:.1%}",
        "年化收益": f"{ann_return:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率": f"{win_rate:.0%}",
        "买入次数": len(trades_df[trades_df["action"] == "买入"]),
        "卖出次数": len(sell_trades),
        "止损次数": len(sl_df),
        "回测天数": len(equity_df),
        "回测年数": f"{years:.1f}",
    }
    
    return metrics


def generate_report(equity_df, trades_df, sl_df, metrics):
    """生成HTML报告"""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    equity_df.to_csv(f"{OUTPUT_DIR}/equity.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(f"{OUTPUT_DIR}/trades.csv", index=False, encoding="utf-8-sig")
    if len(sl_df) > 0:
        sl_df.to_csv(f"{OUTPUT_DIR}/stop_loss_events.csv", index=False, encoding="utf-8-sig")
    
    metrics_html = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
    
    recent_trades = trades_df.tail(20)
    trades_html = ""
    for _, row in recent_trades.iterrows():
        color = "green" if row.get("pnl_pct", 0) > 0 else "red" if row.get("pnl_pct", 0) < 0 else "black"
        trades_html += f"""<tr>
            <td>{row['date']}</td>
            <td>{row['code']}</td>
            <td>{row['action']}</td>
            <td>{row['reason']}</td>
            <td>{row['price']:.2f}</td>
            <td>{row['shares']}</td>
            <td style="color:{color}">{row.get('pnl_pct', 0):.1f}%</td>
        </tr>"""
    
    sl_html = ""
    if len(sl_df) > 0:
        for _, row in sl_df.iterrows():
            sl_html += f"""<tr>
                <td>{row['date']}</td>
                <td>{row['code']}</td>
                <td>{row['entry_price']:.2f}</td>
                <td>{row['exit_price']:.2f}</td>
                <td style="color:red">{row['loss_pct']:.1f}%</td>
            </tr>"""
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>双均线策略回测报告</title>
    <style>
        body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        .card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; }}
        h2 {{ color: #666; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
        th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: center; }}
        th {{ background: #f0f0f0; }}
        .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
        .metric-box {{ background: #f8f9fa; padding: 15px; border-radius: 5px; text-align: center; }}
        .metric-value {{ font-size: 24px; font-weight: bold; color: #333; }}
        .metric-label {{ font-size: 12px; color: #666; margin-top: 5px; }}
        .positive {{ color: green; }}
        .negative {{ color: red; }}
    </style>
</head>
<body>
<div class="container">
    <div class="card">
        <h1>双均线策略回测报告</h1>
        <p>策略: SMA 10/30 + 全A + {STOP_LOSS:.0%}止损</p>
        <p>回测区间: 2020-01-01 ~ 2026-06-30</p>
        <p>初始资金: {INITIAL_CAPITAL:,.0f}元</p>
        <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
    </div>
    
    <div class="card">
        <h2>绩效指标</h2>
        <div class="metric-grid">
            <div class="metric-box">
                <div class="metric-value">{metrics.get('总收益', 'N/A')}</div>
                <div class="metric-label">总收益</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{metrics.get('年化收益', 'N/A')}</div>
                <div class="metric-label">年化收益</div>
            </div>
            <div class="metric-box">
                <div class="metric-value negative">{metrics.get('最大回撤', 'N/A')}</div>
                <div class="metric-label">最大回撤</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{metrics.get('夏普比率', 'N/A')}</div>
                <div class="metric-label">夏普比率</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{metrics.get('胜率', 'N/A')}</div>
                <div class="metric-label">胜率</div>
            </div>
            <div class="metric-box">
                <div class="metric-value">{metrics.get('止损次数', '0')}</div>
                <div class="metric-label">止损次数</div>
            </div>
        </div>
    </div>
    
    <div class="card">
        <h2>详细指标</h2>
        <table>
            <tr><th>指标</th><th>值</th></tr>
            {metrics_html}
        </table>
    </div>
    
    <div class="card">
        <h2>最近20笔交易</h2>
        <table>
            <tr><th>日期</th><th>代码</th><th>方向</th><th>原因</th><th>价格</th><th>数量</th><th>盈亏</th></tr>
            {trades_html}
        </table>
    </div>
    
    <div class="card">
        <h2>止损事件 ({len(sl_df)} 次)</h2>
        <table>
            <tr><th>日期</th><th>代码</th><th>买入价</th><th>卖出价</th><th>亏损</th></tr>
            {sl_html}
        </table>
    </div>
</div>
</body>
</html>"""
    
    report_path = f"{OUTPUT_DIR}/dual_ma_report.html"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"  报告: {report_path}")
    return report_path


if __name__ == "__main__":
    run_backtest()
