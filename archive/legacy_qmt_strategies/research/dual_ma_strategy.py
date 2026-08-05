# coding=utf-8
"""双均线策略回测：SMA 10/30 + 全A + 5%止损"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime


# ==================== 配置 ====================
DATA_DIR = "E:/astock"
DAILY_PATH = f"{DATA_DIR}/daily/stock_daily.parquet"

# 策略参数
SHORT_MA = 10  # 短期均线
LONG_MA = 30   # 长期均线
STOP_LOSS = -0.05  # 止损线 -5%
MAX_POSITIONS = 50  # 最大持仓数
INITIAL_CAPITAL = 100000  # 初始资金 10万

# 回测区间
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"

# 交易成本
COMMISSION_RATE = 0.0003  # 佣金万三
STAMP_TAX = 0.001  # 印花税千一（卖出）
SLIPPAGE = 0.001  # 滑点千一

OUTPUT_DIR = "D:/QMT_STRATEGIES/research/dual_ma_reports"


def load_data():
    """加载日线数据，计算均线和信号"""
    print("[1/5] 加载数据...")
    daily = pd.read_parquet(DAILY_PATH)
    
    # 过滤日期范围
    idx = daily.index
    trade_dates = idx.get_level_values("trade_date")
    start_ts = pd.Timestamp(START_DATE).date()
    end_ts = pd.Timestamp(END_DATE).date()
    mask = (trade_dates >= start_ts) & (trade_dates <= end_ts)
    daily = daily.loc[mask].copy()
    
    # 去除ST和停牌
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    daily = daily.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])].copy()
    
    print(f"  数据量: {len(daily)} 行, {daily.index.get_level_values('ts_code').nunique()} 只股票")
    return daily


def compute_signals(daily):
    """计算均线和交易信号"""
    print("[2/5] 计算均线信号...")
    
    # 按股票分组计算均线
    signals = []
    codes = daily.index.get_level_values("ts_code").unique()
    
    for code in codes:
        stock_data = daily.loc[daily.index.get_level_values("ts_code") == code].copy()
        if len(stock_data) < LONG_MA + 10:
            continue
        
        close = stock_data["close"]
        
        # 计算均线
        stock_data["sma_short"] = close.rolling(SHORT_MA).mean()
        stock_data["sma_long"] = close.rolling(LONG_MA).mean()
        
        # 计算金叉死叉信号
        stock_data["golden_cross"] = (
            (stock_data["sma_short"] > stock_data["sma_long"]) & 
            (stock_data["sma_short"].shift(1) <= stock_data["sma_long"].shift(1))
        )
        stock_data["death_cross"] = (
            (stock_data["sma_short"] < stock_data["sma_long"]) & 
            (stock_data["sma_short"].shift(1) >= stock_data["sma_long"].shift(1))
        )
        
        # 计算趋势状态：1=多头排列，0=空头排列
        stock_data["trend"] = (stock_data["sma_short"] > stock_data["sma_long"]).astype(int)
        
        signals.append(stock_data)
    
    signals_df = pd.concat(signals)
    print(f"  信号计算完成: {len(signals_df)} 行")
    return signals_df


def run_backtest(signals_df):
    """执行回测"""
    print("[3/5] 执行回测...")
    
    trade_dates = sorted(signals_df.index.get_level_values("trade_date").unique())
    codes = signals_df.index.get_level_values("ts_code").unique()
    
    # 初始化
    capital = INITIAL_CAPITAL
    positions = {}  # code -> {shares, entry_price, entry_date}
    equity_curve = []
    trades = []
    stop_loss_events = []
    
    for i, date in enumerate(trade_dates):
        if i < LONG_MA + 5:  # 预热期
            continue
        
        # 获取当日数据
        today_data = signals_df.loc[date]
        
        # ====== 止损检查 ======
        codes_to_sell = []
        for code, pos in list(positions.items()):
            if code not in today_data.index:
                continue
            current_price = today_data.loc[code, "close"] if code in today_data.index else None
            if current_price is None or pd.isna(current_price):
                continue
            
            ret = current_price / pos["entry_price"] - 1.0
            if ret <= STOP_LOSS:
                codes_to_sell.append((code, "止损", current_price))
        
        # ====== 死叉卖出 ======
        for code, pos in list(positions.items()):
            if code in today_data.index and today_data.loc[code, "death_cross"]:
                current_price = today_data.loc[code, "close"]
                if not pd.isna(current_price):
                    codes_to_sell.append((code, "死叉", current_price))
        
        # 执行卖出
        for code, reason, sell_price in codes_to_sell:
            if code not in positions:
                continue
            pos = positions[code]
            sell_amount = pos["shares"] * sell_price
            sell_cost = sell_amount * (COMMISSION_RATE + STAMP_TAX + SLIPPAGE)
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
        
        # ====== 金叉买入 ======
        # 筛选当日金叉且多头排列的股票
        candidates = []
        for code in codes:
            if code in today_data.index:
                row = today_data.loc[code]
                if row.get("golden_cross") and row.get("trend") == 1:
                    # 计算20日涨幅作为动量过滤
                    if code in signals_df.index.get_level_values("ts_code"):
                        stock_hist = signals_df.loc[signals_df.index.get_level_values("ts_code") == code]
                        if len(stock_hist) >= 20:
                            recent = stock_hist.tail(20)
                            if date in recent.index.get_level_values("trade_date"):
                                momentum = recent.loc[date, "close"] / recent.iloc[0]["close"] - 1
                                if 0 < momentum < 0.3:  # 过滤暴涨股
                                    candidates.append((code, momentum))
        
        # 按动量排序，选最强的
        candidates.sort(key=lambda x: x[1], reverse=True)
        n_buy = min(MAX_POSITIONS - len(positions), len(candidates))
        
        for code, _ in candidates[:n_buy]:
            if code not in today_data.index:
                continue
            buy_price = today_data.loc[code, "close"]
            if pd.isna(buy_price) or buy_price <= 0:
                continue
            
            # 等权分配资金
            alloc = capital / (n_buy - candidates[:n_buy].index((code, _)) + len(positions))
            alloc = min(alloc, capital * 0.95)  # 保留5%现金
            
            if alloc < 1000:  # 最小买入金额
                continue
            
            shares = int(alloc / buy_price / 100) * 100  # 整手
            if shares < 100:
                continue
            
            buy_amount = shares * buy_price
            buy_cost = buy_amount * (COMMISSION_RATE + SLIPPAGE)
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
                "reason": "金叉",
                "price": buy_price,
                "shares": shares,
                "pnl_pct": 0,
            })
        
        # ====== 记录净值 ======
        portfolio_value = capital
        for code, pos in positions.items():
            if code in today_data.index:
                current_price = today_data.loc[code, "close"]
                if not pd.isna(current_price):
                    portfolio_value += pos["shares"] * current_price
        
        equity_curve.append({
            "date": date,
            "portfolio_value": portfolio_value,
            "capital": capital,
            "n_positions": len(positions),
        })
    
    equity_df = pd.DataFrame(equity_curve)
    trades_df = pd.DataFrame(trades)
    sl_df = pd.DataFrame(stop_loss_events)
    
    print(f"  回测完成: {len(equity_df)} 个交易日, {len(trades)} 笔交易")
    return equity_df, trades_df, sl_df


def calc_metrics(equity_df, trades_df, sl_df):
    """计算绩效指标"""
    print("[4/5] 计算绩效指标...")
    
    metrics = {}
    
    if len(equity_df) == 0:
        return metrics
    
    # 总收益
    total_return = equity_df["portfolio_value"].iloc[-1] / INITIAL_CAPITAL - 1.0
    
    # 年化收益
    first_date = pd.Timestamp(equity_df["date"].iloc[0])
    last_date = pd.Timestamp(equity_df["date"].iloc[-1])
    years = (last_date - first_date).days / 365.25
    years = max(years, 1/12)
    ann_return = (1 + total_return) ** (1 / years) - 1
    
    # 最大回撤
    cummax = equity_df["portfolio_value"].cummax()
    drawdown = equity_df["portfolio_value"] / cummax - 1
    max_dd = drawdown.min()
    
    # 日收益率
    equity_df["daily_return"] = equity_df["portfolio_value"].pct_change()
    daily_returns = equity_df["daily_return"].dropna()
    
    # 夏普比率
    rf_daily = 0.025 / 252  # 无风险利率2.5%
    excess_returns = daily_returns - rf_daily
    if excess_returns.std() > 0:
        sharpe = np.sqrt(252) * excess_returns.mean() / excess_returns.std()
    else:
        sharpe = 0
    
    # 胜率
    if len(trades_df) > 0:
        buy_trades = trades_df[trades_df["action"] == "买入"]
        sell_trades = trades_df[trades_df["action"] == "卖出"]
        win_trades = sell_trades[sell_trades["pnl_pct"] > 0]
        win_rate = len(win_trades) / len(sell_trades) if len(sell_trades) > 0 else 0
    else:
        win_rate = 0
    
    # 交易统计
    n_trades = len(trades_df)
    n_buys = len(trades_df[trades_df["action"] == "买入"])
    n_sells = len(trades_df[trades_df["action"] == "卖出"])
    n_sl = len(sl_df)
    
    metrics = {
        "总收益": f"{total_return:.1%}",
        "年化收益": f"{ann_return:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
        "胜率": f"{win_rate:.0%}",
        "买入次数": n_buys,
        "卖出次数": n_sells,
        "止损次数": n_sl,
        "回测天数": len(equity_df),
        "回测年数": f"{years:.1f}",
    }
    
    return metrics


def generate_report(equity_df, trades_df, sl_df, metrics):
    """生成HTML报告"""
    print("[5/5] 生成报告...")
    
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    
    # 保存CSV
    equity_df.to_csv(f"{OUTPUT_DIR}/equity.csv", index=False, encoding="utf-8-sig")
    trades_df.to_csv(f"{OUTPUT_DIR}/trades.csv", index=False, encoding="utf-8-sig")
    if len(sl_df) > 0:
        sl_df.to_csv(f"{OUTPUT_DIR}/stop_loss_events.csv", index=False, encoding="utf-8-sig")
    
    # 生成净值曲线数据（用于图表）
    equity_chart = equity_df[["date", "portfolio_value"]].copy()
    equity_chart["benchmark"] = equity_chart["portfolio_value"] / equity_chart["portfolio_value"].iloc[0]
    
    # HTML报告
    metrics_html = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in metrics.items())
    
    # 最近交易
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
    
    # 止损事件
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
        <p>策略: SMA {SHORT_MA}/{LONG_MA} + 全A + {STOP_LOSS:.0%}止损</p>
        <p>回测区间: {START_DATE} ~ {END_DATE}</p>
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
    
    print(f"  报告已生成: {report_path}")
    return report_path


def main():
    """主函数"""
    print("=" * 60)
    print("双均线策略回测")
    print(f"策略: SMA {SHORT_MA}/{LONG_MA} + 全A + {STOP_LOSS:.0%}止损")
    print(f"回测区间: {START_DATE} ~ {END_DATE}")
    print("=" * 60)
    
    # 执行回测流程
    daily = load_data()
    signals_df = compute_signals(daily)
    equity_df, trades_df, sl_df = run_backtest(signals_df)
    metrics = calc_metrics(equity_df, trades_df, sl_df)
    
    # 输出结果
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    for k, v in metrics.items():
        print(f"{k}: {v}")
    
    # 生成报告
    report_path = generate_report(equity_df, trades_df, sl_df, metrics)
    
    print("\n" + "=" * 60)
    print("完成!")
    print(f"报告路径: {report_path}")
    print("=" * 60)
    
    return metrics


if __name__ == "__main__":
    main()
