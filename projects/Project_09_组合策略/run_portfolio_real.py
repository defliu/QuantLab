# coding=utf-8
"""
真实组合回测：70%红利低波 + 30%质量小市值
合并两个策略的选股逻辑，动态分配资金
"""

import pandas as pd
import numpy as np
from pathlib import Path

# 配置
DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
FINANCE_PATH = "E:/astock/finance/fina_indicator.parquet"
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"
INITIAL_CAPITAL = 1_000_000
WEIGHT_DIVIDEND = 0.70  # 红利低波权重
WEIGHT_SMALLCAP = 0.30  # 质量小市值权重
COMMISSION = 0.00025
STAMP_TAX = 0.001
SLIPPAGE = 0.002

# ==================== 策略逻辑 ====================

def select_dividend_stocks(date, close, fin_map, max_stocks=30):
    """红利低波选股"""
    if date not in close.index:
        return []
    
    # 质量过滤：ROE>8%, 负债率<60%, 现金流>0
    quality_codes = set()
    for code, info in fin_map.items():
        if (info.get('roe', 0) > 8.0 and 
            info.get('debt_to_assets', 999) < 60.0 and 
            info.get('ocfps', 0) > 0):
            quality_codes.add(code)
    
    # 低波动：20日波动率低于中位数
    if date not in close.index:
        return []
    
    returns = close.pct_change()
    vol_20 = returns.rolling(20).std()
    
    if date in vol_20.index:
        vol_today = vol_20.loc[date].dropna()
        vol_median = vol_today.median()
        low_vol_codes = set(vol_today[vol_today <= vol_median].index)
    else:
        low_vol_codes = set()
    
    # 交集
    target_codes = quality_codes.intersection(low_vol_codes)
    
    # 按市值排序（成交额低的小盘优先）
    return list(target_codes)[:max_stocks]


def select_smallcap_stocks(date, close, amount, fin_map, max_stocks=40):
    """质量小市值选股"""
    if date not in close.index or date not in amount.index:
        return []
    
    # 质量过滤：ROE>5%, 负债率<70%, 现金流>0
    quality_codes = set()
    for code, info in fin_map.items():
        if (info.get('roe', 0) > 5.0 and 
            info.get('debt_to_assets', 999) < 70.0 and 
            info.get('ocfps', 0) > 0):
            quality_codes.add(code)
    
    # 小盘：成交额最低30%
    amt_today = amount.loc[date].dropna()
    if len(amt_today) > 100:
        threshold = amt_today.quantile(0.30)
        small_cap_codes = set(amt_today[amt_today <= threshold].index)
    else:
        small_cap_codes = set()
    
    # 交集
    target_codes = quality_codes.intersection(small_cap_codes)
    
    # 按反转因子排序：20日跌幅最大的优先
    reversal = -(close / close.shift(20) - 1)
    if date in reversal.index:
        rev_today = reversal.loc[date]
        candidates = [(c, rev_today.get(c, 0)) for c in target_codes if c in rev_today.index]
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in candidates[:max_stocks]]
    
    return list(target_codes)[:max_stocks]


# ==================== 回测引擎 ====================

def run_backtest():
    print("=" * 60)
    print("真实组合回测：70%红利低波 + 30%质量小市值")
    print("=" * 60)
    
    # 加载数据
    print("\n[1/3] 加载数据...")
    daily = pd.read_parquet(DAILY_PATH)
    
    idx = daily.index
    trade_dates = idx.get_level_values("trade_date")
    mask = (trade_dates >= pd.Timestamp(START_DATE).date()) & (trade_dates <= pd.Timestamp(END_DATE).date())
    daily = daily.loc[mask].copy()
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    daily = daily.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])].copy()
    
    # 构建面板
    close = daily["close"].unstack("ts_code")
    amount = daily["amount"].unstack("ts_code")
    
    # 加载财务数据
    print("[2/3] 加载财务数据...")
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[["ts_code", "ann_date", "roe", "debt_to_assets", "ocfps"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], format="%Y%m%d", errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin = fin.sort_values("ann_date").groupby("ts_code").last().reset_index()
    
    fin_map = {}
    for _, row in fin.iterrows():
        fin_map[row["ts_code"]] = {
            "roe": row.get("roe", 0),
            "debt_to_assets": row.get("debt_to_assets", 999),
            "ocfps": row.get("ocfps", 0),
        }
    
    trade_dates_list = sorted(close.index.tolist())
    
    # 初始化
    print("[3/3] 执行回测...")
    capital = INITIAL_CAPITAL
    positions_dividend = {}  # 红利低波持仓
    positions_smallcap = {}  # 质量小市值持仓
    equity_curve = []
    trades = []
    last_rebal_week = None
    
    # 资金分配
    capital_dividend = INITIAL_CAPITAL * WEIGHT_DIVIDEND
    capital_smallcap = INITIAL_CAPITAL * WEIGHT_SMALLCAP
    
    for i, date in enumerate(trade_dates_list):
        if i % 200 == 0:
            print(f"  {i}/{len(trade_dates_list)} ({date})")
        
        # 每周调仓
        current_week = date.isocalendar()[1]
        is_rebal = (last_rebal_week is None) or (current_week != last_rebal_week)
        
        # ====== 止损检查 ======
        # 红利低波止损
        for code in list(positions_dividend.keys()):
            pos = positions_dividend[code]
            if date in close.index and code in close.columns:
                cp = close.loc[date, code]
                if not pd.isna(cp) and cp > 0:
                    if cp / pos["price"] - 1 <= -0.12:  # 止损-12%
                        sell_amount = pos["shares"] * cp * 0.997
                        capital_dividend += sell_amount
                        trades.append({"date": date, "code": code, "strategy": "红利低波", 
                                      "reason": "止损", "pnl": (cp/pos["price"]-1)*100})
                        del positions_dividend[code]
        
        # 质量小市值止损
        for code in list(positions_smallcap.keys()):
            pos = positions_smallcap[code]
            if date in close.index and code in close.columns:
                cp = close.loc[date, code]
                if not pd.isna(cp) and cp > 0:
                    if cp / pos["price"] - 1 <= -0.10:  # 止损-10%
                        sell_amount = pos["shares"] * cp * 0.997
                        capital_smallcap += sell_amount
                        trades.append({"date": date, "code": code, "strategy": "质量小市值",
                                      "reason": "止损", "pnl": (cp/pos["price"]-1)*100})
                        del positions_smallcap[code]
        
        # ====== 调仓 ======
        if is_rebal:
            last_rebal_week = current_week
            
            # 红利低波选股
            div_stocks = select_dividend_stocks(date, close, fin_map, 30)
            
            # 卖出不在新列表中的
            for code in list(positions_dividend.keys()):
                if code not in div_stocks:
                    pos = positions_dividend[code]
                    if date in close.index and code in close.columns:
                        sp = close.loc[date, code]
                        if not pd.isna(sp) and sp > 0:
                            capital_dividend += pos["shares"] * sp * 0.997
                            trades.append({"date": date, "code": code, "strategy": "红利低波",
                                          "reason": "调仓", "pnl": (sp/pos["price"]-1)*100})
                    del positions_dividend[code]
            
            # 买入新股票
            n_buy = min(30 - len(positions_dividend), len(div_stocks))
            for code in div_stocks[:n_buy]:
                if code in positions_dividend:
                    continue
                if date in close.index and code in close.columns:
                    bp = close.loc[date, code]
                    if pd.isna(bp) or bp <= 0:
                        continue
                    alloc = capital_dividend / max(1, n_buy + len(positions_dividend))
                    alloc = min(alloc, capital_dividend * 0.95)
                    if alloc < 10000:
                        continue
                    shares = int(alloc / bp / 100) * 100
                    if shares < 100:
                        continue
                    capital_dividend -= shares * bp * 1.002
                    positions_dividend[code] = {"shares": shares, "price": bp}
                    trades.append({"date": date, "code": code, "strategy": "红利低波",
                                  "reason": "买入", "pnl": 0})
            
            # 质量小市值选股
            sc_stocks = select_smallcap_stocks(date, close, amount, fin_map, 40)
            
            # 卖出不在新列表中的
            for code in list(positions_smallcap.keys()):
                if code not in sc_stocks:
                    pos = positions_smallcap[code]
                    if date in close.index and code in close.columns:
                        sp = close.loc[date, code]
                        if not pd.isna(sp) and sp > 0:
                            capital_smallcap += pos["shares"] * sp * 0.997
                            trades.append({"date": date, "code": code, "strategy": "质量小市值",
                                          "reason": "调仓", "pnl": (sp/pos["price"]-1)*100})
                    del positions_smallcap[code]
            
            # 买入新股票
            n_buy = min(40 - len(positions_smallcap), len(sc_stocks))
            for code in sc_stocks[:n_buy]:
                if code in positions_smallcap:
                    continue
                if date in close.index and code in close.columns:
                    bp = close.loc[date, code]
                    if pd.isna(bp) or bp <= 0:
                        continue
                    alloc = capital_smallcap / max(1, n_buy + len(positions_smallcap))
                    alloc = min(alloc, capital_smallcap * 0.95)
                    if alloc < 10000:
                        continue
                    shares = int(alloc / bp / 100) * 100
                    if shares < 100:
                        continue
                    capital_smallcap -= shares * bp * 1.002
                    positions_smallcap[code] = {"shares": shares, "price": bp}
                    trades.append({"date": date, "code": code, "strategy": "质量小市值",
                                  "reason": "买入", "pnl": 0})
        
        # ====== 记录净值 ======
        pv_dividend = capital_dividend
        for c, p in positions_dividend.items():
            if date in close.index and c in close.columns:
                cp = close.loc[date, c]
                if not pd.isna(cp):
                    pv_dividend += p["shares"] * cp
        
        pv_smallcap = capital_smallcap
        for c, p in positions_smallcap.items():
            if date in close.index and c in close.columns:
                cp = close.loc[date, c]
                if not pd.isna(cp):
                    pv_smallcap += p["shares"] * cp
        
        portfolio_value = pv_dividend + pv_smallcap
        
        equity_curve.append({
            "date": date,
            "value": portfolio_value,
            "dividend_value": pv_dividend,
            "smallcap_value": pv_smallcap,
            "n_positions": len(positions_dividend) + len(positions_smallcap),
        })
    
    # 计算指标
    eq = pd.DataFrame(equity_curve)
    if len(eq) == 0:
        print("无数据")
        return
    
    total_ret = eq["value"].iloc[-1] / INITIAL_CAPITAL - 1
    years = max((pd.Timestamp(eq["date"].iloc[-1]) - pd.Timestamp(eq["date"].iloc[0])).days / 365.25, 1/12)
    ann_ret = (1 + total_ret) ** (1/years) - 1
    max_dd = (eq["value"] / eq["value"].cummax() - 1).min()
    daily_ret = eq["value"].pct_change().dropna()
    sharpe = np.sqrt(252) * (daily_ret - 0.025/252).mean() / daily_ret.std() if daily_ret.std() > 0 else 0
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    
    trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
    sells = trades_df[trades_df["reason"].isin(["止损", "调仓"])] if len(trades_df) > 0 else pd.DataFrame()
    win_rate = len(sells[sells["pnl"] > 0]) / len(sells) if len(sells) > 0 else 0
    
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    print(f"  总收益: {total_ret:.1%}")
    print(f"  年化收益: {ann_ret:.1%}")
    print(f"  最大回撤: {max_dd:.1%}")
    print(f"  夏普比率: {sharpe:.2f}")
    print(f"  Calmar比率: {calmar:.2f}")
    print(f"  胜率: {win_rate:.0%}")
    print(f"  交易次数: {len(trades_df)}")
    
    # 分年统计
    print("\n分年表现:")
    eq['year'] = pd.to_datetime(eq['date']).dt.year
    for year in sorted(eq['year'].unique()):
        year_data = eq[eq['year'] == year]
        if len(year_data) > 1:
            yr_ret = (year_data['value'].iloc[-1] / year_data['value'].iloc[0] - 1) * 100
            yr_dd = ((year_data['value'] / year_data['value'].cummax()) - 1).min() * 100
            print(f"  {year}年: 收益{yr_ret:.1f}%, 回撤{yr_dd:.1f}%")
    
    # 保存
    OUTPUT_DIR = "E:/QuantLab/projects/Project_09_组合策略/results"
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    eq.to_csv(f"{OUTPUT_DIR}/portfolio_real_equity.csv", index=False)
    if len(trades_df) > 0:
        trades_df.to_csv(f"{OUTPUT_DIR}/portfolio_real_trades.csv", index=False)
    
    print(f"\n结果已保存: {OUTPUT_DIR}")


if __name__ == "__main__":
    run_backtest()
