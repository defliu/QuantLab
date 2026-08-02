# coding=utf-8
"""指数增强策略回测 - 简化版"""

import pandas as pd
import numpy as np
from pathlib import Path

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
FINANCE_PATH = "E:/astock/finance/fina_indicator.parquet"
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"
INITIAL_CAPITAL = 1_000_000
MAX_STOCKS = 80
HOLDING_DAYS = 10
STOP_LOSS = -0.12
COMMISSION = 0.00025
STAMP_TAX = 0.001
SLIPPAGE = 0.001
OUTPUT_DIR = "E:/QuantLab/projects/Project_08_指数增强/results"


def main():
    print("=" * 50)
    print("指数增强策略回测（简化版）")
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
    
    # 取市值最大的800只作为候选池（模拟中证500+中证1000）
    latest_date = daily.index.get_level_values("trade_date").max()
    latest = daily.loc[daily.index.get_level_values("trade_date") == latest_date].copy()
    # 用成交额作为市值代理
    top_stocks = latest.nlargest(800, "amount").index.get_level_values("ts_code").unique()
    daily = daily[daily.index.get_level_values("ts_code").isin(top_stocks)]
    print(f"  候选池: {len(top_stocks)} 只股票")
    
    # 加载财务数据
    print("[2/3] 加载财务数据...")
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[["ts_code", "end_date", "ann_date", "roe", "netprofit_yoy", "or_yoy"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], format="%Y%m%d", errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin = fin.sort_values("ann_date").groupby("ts_code").last().reset_index()
    
    # 构建面板
    print("[3/3] 执行回测...")
    close = daily["close"].unstack("ts_code")
    pct = daily["pct_chg"].unstack("ts_code")
    
    # 因子
    momentum_20 = close / close.shift(20) - 1
    vol_20 = pct.rolling(20).std()
    reversal_5 = -(close / close.shift(5) - 1)
    
    trade_dates_list = sorted(close.index.tolist())
    
    # 财务映射
    fin_map = {}
    for _, row in fin.iterrows():
        fin_map[row["ts_code"]] = {
            "roe": row.get("roe", 0),
            "np_yoy": row.get("netprofit_yoy", 0),
        }
    
    # 回测
    capital = INITIAL_CAPITAL
    positions = {}
    equity_curve = []
    trades = []
    last_rebal_idx = -HOLDING_DAYS
    
    for i, date in enumerate(trade_dates_list):
        if i % 200 == 0:
            print(f"  {i}/{len(trade_dates_list)} ({date})")
        
        # 卖出止损
        codes_to_sell = [c for c, p in positions.items()
                        if i - p["idx"] >= 5 and date in close.index and c in close.columns
                        and close.loc[date, c] / p["price"] - 1 <= STOP_LOSS]
        
        for code in codes_to_sell:
            pos = positions[code]
            if date in close.index and code in close.columns:
                sp = close.loc[date, code]
                if not pd.isna(sp) and sp > 0:
                    capital += pos["shares"] * sp * 0.997
                    trades.append({"date": date, "code": code, "reason": "止损",
                                  "pnl": (sp/pos["price"]-1)*100})
            del positions[code]
        
        # 调仓
        if i - last_rebal_idx >= HOLDING_DAYS:
            last_rebal_idx = i
            
            # 多因子打分
            if date in momentum_20.index:
                mom_today = momentum_20.loc[date]
                vol_today = vol_20.loc[date] if date in vol_20.index else pd.Series()
                rev_today = reversal_5.loc[date] if date in reversal_5.index else pd.Series()
                
                candidates = []
                for code in close.columns:
                    if code not in fin_map:
                        continue
                    
                    scores = []
                    # 动量因子
                    if code in mom_today.index and not pd.isna(mom_today[code]):
                        scores.append(("mom", mom_today[code]))
                    # 低波因子（取负）
                    if code in vol_today.index and not pd.isna(vol_today[code]):
                        scores.append(("vol", -vol_today[code]))
                    # 反转因子
                    if code in rev_today.index and not pd.isna(rev_today[code]):
                        scores.append(("rev", rev_today[code]))
                    # ROE
                    roe = fin_map[code].get("roe", 0)
                    if roe > 0:
                        scores.append(("roe", roe / 100))
                    
                    if len(scores) >= 3:
                        # 简单等权合成
                        avg_score = np.mean([s[1] for s in scores])
                        candidates.append((code, avg_score))
                
                # 排序取Top N
                candidates.sort(key=lambda x: x[1], reverse=True)
                new_stocks = [c[0] for c in candidates[:MAX_STOCKS]]
                
                # 卖出不在新列表中的
                for code in list(positions.keys()):
                    if code not in new_stocks:
                        pos = positions[code]
                        if date in close.index and code in close.columns:
                            sp = close.loc[date, code]
                            if not pd.isna(sp) and sp > 0:
                                capital += pos["shares"] * sp * 0.997
                                trades.append({"date": date, "code": code, "reason": "调仓",
                                              "pnl": (sp/pos["price"]-1)*100})
                        del positions[code]
                
                # 买入
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
                        capital -= shares * bp * 1.001
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
    eq.to_csv(f"{OUTPUT_DIR}/index_equity.csv", index=False)
    if len(trades_df) > 0:
        trades_df.to_csv(f"{OUTPUT_DIR}/index_trades.csv", index=False)
    
    print(f"\n结果: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
