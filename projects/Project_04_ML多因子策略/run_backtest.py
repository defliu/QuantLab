# coding=utf-8
"""LightGBM多因子策略回测 - 优化版v2"""

import os
import sys
import pandas as pd
import numpy as np
import lightgbm as lgb
from pathlib import Path

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
INITIAL_CAPITAL = 100000
MAX_POSITIONS = 30
HOLDING_DAYS = 5
TRAIN_WINDOW = 504
FORWARD_DAYS = 5
OUTPUT_DIR = "E:/QuantLab/projects/Project_04_ML多因子策略/reports"


def main():
    print("=" * 50)
    print("LightGBM多因子策略回测")
    print("=" * 50)
    
    # 加载数据
    print("\n[1/3] 加载数据...")
    daily = pd.read_parquet(DAILY_PATH)
    
    idx = daily.index
    trade_dates = idx.get_level_values("trade_date")
    mask = (trade_dates >= pd.Timestamp("2020-01-01").date()) & (trade_dates <= pd.Timestamp("2026-06-30").date())
    daily = daily.loc[mask].copy()
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    daily = daily.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])].copy()
    print(f"  {len(daily)} 行, {daily.index.get_level_values('ts_code').nunique()} 只股票")
    
    # 构建面板
    print("[2/3] 计算因子...")
    close = daily["close"].unstack("ts_code")
    pct = daily["pct_chg"].unstack("ts_code")
    
    trade_dates_list = sorted(close.index.tolist())
    feature_cols = ["mom5", "mom20", "rev5", "vol20", "ma5_bias", "ma20_bias"]
    
    # 预计算所有因子（DataFrame格式，index=trade_date, columns=ts_code）
    print("  计算因子...")
    factors_mom5 = close / close.shift(5) - 1
    factors_mom20 = close / close.shift(20) - 1
    factors_rev5 = -(close / close.shift(5) - 1)
    factors_vol20 = pct.rolling(20).std()
    factors_ma5_bias = (close - close.rolling(5).mean()) / close.rolling(5).mean()
    factors_ma20_bias = (close - close.rolling(20).mean()) / close.rolling(20).mean()
    factors_label = close.shift(-FORWARD_DAYS) / close - 1
    
    # 回测
    print("[3/3] 执行回测...")
    capital = INITIAL_CAPITAL
    positions = {}
    equity_curve = []
    trades = []
    model = None
    
    for i in range(TRAIN_WINDOW, len(trade_dates_list), HOLDING_DAYS):
        date = trade_dates_list[i]
        if i % 100 == 0:
            print(f"  {i}/{len(trade_dates_list)} ({date})")
        
        # 每月重训
        if i % 21 == 0 or model is None:
            train_start = max(0, i - TRAIN_WINDOW)
            train_dates = trade_dates_list[train_start:i]
            
            # 收集训练数据
            X_list = []
            y_list = []
            for td in train_dates:
                if td not in factors_mom5.index:
                    continue
                row_mom5 = factors_mom5.loc[td]
                row_mom20 = factors_mom20.loc[td]
                row_rev5 = factors_rev5.loc[td]
                row_vol20 = factors_vol20.loc[td]
                row_ma5 = factors_ma5_bias.loc[td]
                row_ma20 = factors_ma20_bias.loc[td]
                row_label = factors_label.loc[td]
                
                # 取有效股票
                valid = row_mom5.dropna().index.intersection(
                    row_mom20.dropna().index).intersection(
                    row_label.dropna().index)
                
                for code in valid:
                    X_list.append([
                        row_mom5[code], row_mom20[code], row_rev5[code],
                        row_vol20[code], row_ma5[code], row_ma20[code]
                    ])
                    y_list.append(row_label[code])
            
            if len(X_list) > 1000:
                X = np.array(X_list)
                y = np.array(y_list)
                ds = lgb.Dataset(X, label=y)
                params = {'objective': 'regression', 'metric': 'mse', 'num_leaves': 31,
                         'learning_rate': 0.05, 'feature_fraction': 0.8, 'verbose': -1}
                model = lgb.train(params, ds, num_boost_round=50)
        
        if model is None:
            continue
        
        # 预测今日
        if date not in factors_mom5.index:
            continue
        
        row_mom5 = factors_mom5.loc[date]
        row_mom20 = factors_mom20.loc[date]
        row_rev5 = factors_rev5.loc[date]
        row_vol20 = factors_vol20.loc[date]
        row_ma5 = factors_ma5_bias.loc[date]
        row_ma20 = factors_ma20_bias.loc[date]
        
        valid = row_mom5.dropna().index.intersection(
            row_mom20.dropna().index).intersection(
            row_vol20.dropna().index)
        
        if len(valid) == 0:
            continue
        
        X_pred = np.array([
            [row_mom5[c], row_mom20[c], row_rev5[c], row_vol20[c], row_ma5[c], row_ma20[c]]
            for c in valid
        ])
        predictions = model.predict(X_pred)
        pred_series = pd.Series(predictions, index=valid)
        candidates = pred_series.sort_values(ascending=False).head(MAX_POSITIONS).index.tolist()
        
        # 卖出到期
        for code in [c for c, p in positions.items() if i - p["idx"] >= HOLDING_DAYS]:
            pos = positions[code]
            if date in close.index:
                sp = close.loc[date].get(code)
                if sp and not pd.isna(sp):
                    capital += pos["shares"] * sp * 0.998
                    trades.append({"pnl": (sp/pos["price"]-1)*100})
            del positions[code]
        
        # 买入
        n_buy = min(MAX_POSITIONS - len(positions), len(candidates))
        for code in candidates[:n_buy]:
            if code in positions:
                continue
            bp = close.loc[date].get(code)
            if not bp or pd.isna(bp) or bp <= 0:
                continue
            alloc = min(capital / max(1, n_buy + len(positions)), capital * 0.95)
            if alloc < 1000:
                continue
            shares = int(alloc / bp / 100) * 100
            if shares < 100:
                continue
            capital -= shares * bp * 1.002
            positions[code] = {"shares": shares, "price": bp, "idx": i}
        
        # 记录净值
        pv = capital
        for c, p in positions.items():
            if date in close.index:
                cp = close.loc[date].get(c)
                if cp and not pd.isna(cp):
                    pv += p["shares"] * cp
        equity_curve.append({"date": date, "value": pv})
    
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
    sharpe = np.sqrt(252) * daily_ret.mean() / daily_ret.std() if daily_ret.std() > 0 else 0
    
    print("\n" + "=" * 50)
    print("回测结果")
    print("=" * 50)
    print(f"  总收益: {total_ret:.1%}")
    print(f"  年化收益: {ann_ret:.1%}")
    print(f"  最大回撤: {max_dd:.1%}")
    print(f"  夏普比率: {sharpe:.2f}")
    print(f"  交易次数: {len(trades)}")
    
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    eq.to_csv(f"{OUTPUT_DIR}/equity.csv", index=False)
    print(f"\n报告: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
