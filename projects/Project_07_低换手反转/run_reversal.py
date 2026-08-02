# coding=utf-8
"""低换手率+短期反转策略回测 - astock parquet"""

import pandas as pd
import numpy as np
from pathlib import Path

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"
INITIAL_CAPITAL = 1_000_000
MAX_STOCKS = 50
HOLDING_DAYS = 5
STOP_LOSS = -0.07
COMMISSION = 0.00015  # 万1.5（低换手策略对佣金敏感）
STAMP_TAX = 0.001
SLIPPAGE = 0.002
OUTPUT_DIR = "E:/QuantLab/projects/Project_07_低换手反转/results"


def main():
    print("=" * 50)
    print("低换手率+短期反转策略回测")
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
    
    # 构建面板
    print("[2/3] 计算因子...")
    close = daily["close"].unstack("ts_code")
    amount = daily["amount"].unstack("ts_code")
    vol = daily["vol"].unstack("ts_code")
    
    # 20日平均换手率（用成交量代理）
    turnover_20 = vol.rolling(20).mean()
    
    # 5日反转因子
    reversal_5 = -(close / close.shift(5) - 1)
    
    trade_dates_list = sorted(close.index.tolist())
    
    # 回测
    print("[3/3] 执行回测...")
    capital = INITIAL_CAPITAL
    positions = {}
    equity_curve = []
    trades = []
    
    for i, date in enumerate(trade_dates_list):
        if i % 200 == 0:
            print(f"  {i}/{len(trade_dates_list)} ({date})")
        
        # 卖出到期或止损
        codes_to_sell = []
        for code, pos in list(positions.items()):
            hold_days = i - pos["entry_idx"]
            if hold_days >= HOLDING_DAYS:
                codes_to_sell.append((code, "到期"))
                continue
            if date in close.index and code in close.columns:
                cp = close.loc[date, code]
                if not pd.isna(cp) and cp > 0:
                    if cp / pos["entry_price"] - 1 <= STOP_LOSS:
                        codes_to_sell.append((code, "止损"))
        
        for code, reason in codes_to_sell:
            if code not in positions:
                continue
            pos = positions[code]
            if date in close.index and code in close.columns:
                sp = close.loc[date, code]
                if not pd.isna(sp) and sp > 0:
                    capital += pos["shares"] * sp * (1 - COMMISSION - STAMP_TAX - SLIPPAGE)
                    trades.append({"date": date, "code": code, "reason": reason,
                                  "pnl": (sp/pos["entry_price"]-1)*100})
            del positions[code]
        
        # 每5天选股
        if i % HOLDING_DAYS == 0 and date in turnover_20.index:
            to_today = turnover_20.loc[date]
            rev_today = reversal_5.loc[date] if date in reversal_5.index else pd.Series()
            
            # 成交额过滤（>500万）
            if date in amount.index:
                amt_today = amount.loc[date]
                valid_amt = amt_today[amt_today > 500].dropna().index
            else:
                valid_amt = []
            
            # 取换手率最低30%
            valid_to = to_today.dropna()
            if len(valid_to) > 100:
                threshold = valid_to.quantile(0.30)
                low_turnover_codes = set(valid_to[valid_to <= threshold].index)
                
                # 交集
                target_codes = low_turnover_codes.intersection(set(valid_amt))
                
                # 按反转因子排序
                candidates = []
                for code in target_codes:
                    if code in rev_today.index:
                        rev = rev_today[code]
                        if not pd.isna(rev):
                            candidates.append((code, rev))
                
                candidates.sort(key=lambda x: x[1], reverse=True)  # 跌多优先
                new_stocks = [c[0] for c in candidates[:MAX_STOCKS]]
                
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
                        capital -= shares * bp * (1 + COMMISSION + SLIPPAGE)
                        positions[code] = {"shares": shares, "entry_price": bp, "entry_idx": i}
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
    sells = trades_df[trades_df["reason"].isin(["止损", "到期"])] if len(trades_df) > 0 else pd.DataFrame()
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
    eq.to_csv(f"{OUTPUT_DIR}/reversal_equity.csv", index=False)
    if len(trades_df) > 0:
        trades_df.to_csv(f"{OUTPUT_DIR}/reversal_trades.csv", index=False)
    
    print(f"\n结果: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
