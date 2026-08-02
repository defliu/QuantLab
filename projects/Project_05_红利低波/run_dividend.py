# coding=utf-8
"""红利低波质量策略回测 - astock parquet"""

import pandas as pd
import numpy as np
from pathlib import Path

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
FINANCE_PATH = "E:/astock/finance/fina_indicator.parquet"
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"
INITIAL_CAPITAL = 1_000_000
MAX_STOCKS = 30
STOP_LOSS = -0.12
COMMISSION = 0.00025
STAMP_TAX = 0.001
SLIPPAGE = 0.001
OUTPUT_DIR = "E:/QuantLab/projects/Project_05_红利低波/results"


def main():
    print("=" * 50)
    print("红利低波质量策略回测")
    print("=" * 50)
    
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
    print(f"  行情: {daily.index.get_level_values('ts_code').nunique()} 只股票")
    
    # 加载财务数据
    print("[2/3] 加载财务数据...")
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[["ts_code", "end_date", "ann_date", "roe", "debt_to_assets", "ocfps", "eps"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], format="%Y%m%d", errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    
    # 取最新一期财务数据
    fin = fin.sort_values("ann_date").groupby("ts_code").last().reset_index()
    
    # 过滤: ROE>8%, 负债率<60%, 现金流/利润>0.8
    fin = fin[fin["roe"] > 8.0]
    fin = fin[fin["debt_to_assets"] < 60.0]
    fin = fin[fin["ocfps"] > 0]
    valid_codes = set(fin["ts_code"])
    print(f"  财务过滤后: {len(valid_codes)} 只股票")
    
    # 构建价格面板
    print("[3/3] 执行回测...")
    close = daily["close"].unstack("ts_code")
    pct = daily["pct_chg"].unstack("ts_code")
    
    # 计算20日波动率
    vol20 = pct.rolling(20).std()
    
    trade_dates_list = sorted(close.index.tolist())
    
    # 回测：月度调仓
    capital = INITIAL_CAPITAL
    positions = {}
    equity_curve = []
    trades = []
    last_rebal_month = None
    
    for i, date in enumerate(trade_dates_list):
        if i % 200 == 0:
            print(f"  {i}/{len(trade_dates_list)} ({date})")
        
        # 月度调仓检查
        current_month = (date.year, date.month)
        is_rebal = (last_rebal_month is None) or (current_month != last_rebal_month)
        
        # 卖出止损
        codes_to_sell = [c for c, p in positions.items() 
                        if i - p["idx"] >= 21 and date in close.index and c in close.columns
                        and close.loc[date, c] / p["price"] - 1 <= STOP_LOSS]
        
        for code in codes_to_sell:
            pos = positions[code]
            if date in close.index and code in close.columns:
                sp = close.loc[date, code]
                if not pd.isna(sp) and sp > 0:
                    capital += pos["shares"] * sp * (1 - COMMISSION - STAMP_TAX - SLIPPAGE)
                    trades.append({"date": date, "code": code, "reason": "止损",
                                  "pnl": (sp/pos["price"]-1)*100})
            del positions[code]
        
        # 月度调仓
        if is_rebal:
            last_rebal_month = current_month
            
            # 选股: 财务过滤 + 低波动
            if date in vol20.index:
                vol_today = vol20.loc[date]
                vol_median = vol_today.dropna().median()
                
                candidates = []
                for code in valid_codes:
                    if code in close.columns and code in vol_today.index:
                        v = vol_today[code]
                        if not pd.isna(v) and v < vol_median:  # 低波动
                            if date in close.index:
                                cp = close.loc[date, code]
                                if not pd.isna(cp) and cp > 0:
                                    candidates.append((code, v))
                
                # 按波动率排序（越低越好）
                candidates.sort(key=lambda x: x[1])
                new_stocks = [c[0] for c in candidates[:MAX_STOCKS]]
                
                # 卖出不在新列表中的
                for code in list(positions.keys()):
                    if code not in new_stocks:
                        pos = positions[code]
                        if date in close.index and code in close.columns:
                            sp = close.loc[date, code]
                            if not pd.isna(sp) and sp > 0:
                                capital += pos["shares"] * sp * (1 - COMMISSION - STAMP_TAX - SLIPPAGE)
                                trades.append({"date": date, "code": code, "reason": "调仓",
                                              "pnl": (sp/pos["price"]-1)*100})
                        del positions[code]
                
                # 买入新股票
                n_buy = min(MAX_STOCKS - len(positions), len(new_stocks))
                for code in new_stocks[:n_buy]:
                    if code in positions:
                        continue
                    if date in close.index and code in close.columns:
                        bp = close.loc[date, code]
                        if pd.isna(bp) or bp <= 0:
                            continue
                        alloc = capital / max(1, n_buy + len(positions))
                        alloc = min(alloc, capital * 0.95)
                        if alloc < 10000:
                            continue
                        shares = int(alloc / bp / 100) * 100
                        if shares < 100:
                            continue
                        capital -= shares * bp * (1 + COMMISSION + SLIPPAGE)
                        positions[code] = {"shares": shares, "price": bp, "idx": i}
                        trades.append({"date": date, "code": code, "reason": "买入", "pnl": 0})
        
        # 记录净值
        pv = capital
        for c, p in positions.items():
            if date in close.index and c in close.columns:
                cp = close.loc[date, c]
                if not pd.isna(cp):
                    pv += p["shares"] * cp
        equity_curve.append({"date": date, "value": pv, "n_pos": len(positions)})
    
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
    
    print("\n" + "=" * 50)
    print("回测结果")
    print("=" * 50)
    print(f"  总收益: {total_ret:.1%}")
    print(f"  年化收益: {ann_ret:.1%}")
    print(f"  最大回撤: {max_dd:.1%}")
    print(f"  夏普比率: {sharpe:.2f}")
    print(f"  Calmar比率: {calmar:.2f}")
    print(f"  胜率: {win_rate:.0%}")
    print(f"  交易次数: {len(trades_df)}")
    
    # 保存
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    eq.to_csv(f"{OUTPUT_DIR}/dividend_equity.csv", index=False)
    if len(trades_df) > 0:
        trades_df.to_csv(f"{OUTPUT_DIR}/dividend_trades.csv", index=False)
    
    with open(f"{OUTPUT_DIR}/dividend_metrics.txt", "w", encoding="utf-8") as f:
        f.write(f"总收益: {total_ret:.1%}\n年化收益: {ann_ret:.1%}\n最大回撤: {max_dd:.1%}\n")
        f.write(f"夏普比率: {sharpe:.2f}\nCalmar比率: {calmar:.2f}\n胜率: {win_rate:.0%}\n")
    
    print(f"\n结果: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
