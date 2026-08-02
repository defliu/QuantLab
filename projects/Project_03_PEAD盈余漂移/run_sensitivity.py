# coding=utf-8
"""PEAD参数敏感性分析"""

import pandas as pd
import numpy as np
from pathlib import Path

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
FINANCE_PATH = "E:/astock/finance/fina_indicator.parquet"
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"
INITIAL_CAPITAL = 1_000_000
OUTPUT_DIR = "E:/QuantLab/projects/Project_03_PEAD盈余漂移/results"


def run_pead_backtest(close, ann_map, trade_dates_list, 
                     sue_threshold, holding_period, stop_loss, max_stocks=30):
    """运行单次PEAD回测"""
    capital = INITIAL_CAPITAL
    positions = {}
    equity_curve = []
    
    for i, date in enumerate(trade_dates_list):
        # 卖出检查
        codes_to_sell = []
        for code, pos in list(positions.items()):
            hold_days = i - pos["entry_idx"]
            if hold_days >= holding_period:
                codes_to_sell.append(code)
                continue
            if date in close.index and code in close.columns:
                cp = close.loc[date, code]
                if not pd.isna(cp) and cp > 0:
                    if cp / pos["entry_price"] - 1 <= stop_loss:
                        codes_to_sell.append(code)
        
        for code in codes_to_sell:
            if code not in positions:
                continue
            pos = positions[code]
            if date in close.index and code in close.columns:
                sp = close.loc[date, code]
                if not pd.isna(sp) and sp > 0:
                    capital += pos["shares"] * sp * 0.997
            del positions[code]
        
        # 买入新信号
        if date in ann_map and len(positions) < max_stocks:
            for code, sue in ann_map[date]:
                if len(positions) >= max_stocks:
                    break
                if code in positions or code not in close.columns or date not in close.index:
                    continue
                if sue < sue_threshold:
                    continue
                
                bp = close.loc[date, code]
                if pd.isna(bp) or bp <= 0:
                    continue
                
                # 3日后涨幅检查
                future_idx = min(i + 3, len(trade_dates_list) - 1)
                future_date = trade_dates_list[future_idx]
                if future_date in close.index and code in close.columns:
                    fp = close.loc[future_date, code]
                    if not pd.isna(fp) and fp > 0:
                        if fp / bp - 1 > 0.10:
                            continue
                
                alloc = capital / max(1, max_stocks - len(positions))
                alloc = min(alloc, capital * 0.95)
                if alloc < 10000:
                    continue
                shares = int(alloc / bp / 100) * 100
                if shares < 100:
                    continue
                capital -= shares * bp * 1.002
                positions[code] = {"shares": shares, "entry_price": bp, "entry_idx": i}
        
        # 记录净值
        pv = capital
        for c, p in positions.items():
            if date in close.index and c in close.columns:
                cp = close.loc[date, c]
                if not pd.isna(cp):
                    pv += p["shares"] * cp
        equity_curve.append(pv)
    
    if len(equity_curve) == 0:
        return {}
    
    eq = pd.Series(equity_curve)
    total_ret = eq.iloc[-1] / INITIAL_CAPITAL - 1
    years = max(len(eq) / 252, 1/12)
    ann_ret = (1 + total_ret) ** (1/years) - 1
    max_dd = (eq / eq.cummax() - 1).min()
    daily_ret = eq.pct_change().dropna()
    sharpe = np.sqrt(252) * daily_ret.mean() / daily_ret.std() if daily_ret.std() > 0 else 0
    
    return {
        "总收益": f"{total_ret:.1%}",
        "年化收益": f"{ann_ret:.1%}",
        "最大回撤": f"{max_dd:.1%}",
        "夏普比率": f"{sharpe:.2f}",
    }


def main():
    print("=" * 60)
    print("PEAD参数敏感性分析")
    print("=" * 60)
    
    # 加载数据
    print("\n加载数据...")
    daily = pd.read_parquet(DAILY_PATH)
    
    idx = daily.index
    trade_dates = idx.get_level_values("trade_date")
    mask = (trade_dates >= pd.Timestamp(START_DATE).date()) & (trade_dates <= pd.Timestamp(END_DATE).date())
    daily = daily.loc[mask].copy()
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    daily = daily.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])].copy()
    
    # 加载财务数据
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[["ts_code", "end_date", "ann_date", "netprofit_yoy", "or_yoy"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], format="%Y%m%d", errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    start_ts = pd.Timestamp(START_DATE)
    end_ts = pd.Timestamp(END_DATE) + pd.Timedelta(days=90)
    fin = fin[(fin["ann_date"] >= start_ts) & (fin["ann_date"] <= end_ts)]
    fin = fin[~fin["netprofit_yoy"].isna() & ~fin["or_yoy"].isna() & (fin["or_yoy"] > 0)]
    fin["sue"] = fin["netprofit_yoy"] / 100.0
    
    # 构建面板
    close = daily["close"].unstack("ts_code")
    trade_dates_list = sorted(close.index.tolist())
    
    # 构建公告映射
    ann_map = {}
    for _, row in fin.iterrows():
        ann_date = row["ann_date"].date()
        if ann_date not in ann_map:
            ann_map[ann_date] = []
        ann_map[ann_date].append((row["ts_code"], row["sue"]))
    
    # 参数组合
    param_combos = [
        # (sue_threshold, holding_period, stop_loss)
        (0.30, 60, -0.08),  # 基线
        (0.50, 60, -0.08),  # 提高SUE
        (0.80, 60, -0.08),  # 更高SUE
        (0.30, 30, -0.08),  # 缩短持有期
        (0.30, 60, -0.10),  # 放宽止损
        (0.50, 30, -0.10),  # 组合优化
        (0.80, 30, -0.10),  # 激进组合
        (0.50, 45, -0.12),  # 中间方案
    ]
    
    results = []
    for sue_thr, hold_per, stop_loss in param_combos:
        label = f"SUE>{sue_thr:.0%} 持有{hold_per}天 止损{stop_loss:.0%}"
        print(f"\n测试: {label}")
        
        metrics = run_pead_backtest(close, ann_map, trade_dates_list,
                                   sue_thr, hold_per, stop_loss)
        metrics["参数"] = label
        results.append(metrics)
        print(f"  结果: {metrics.get('年化收益', 'N/A')}, 夏普{metrics.get('夏普比率', 'N/A')}")
    
    # 保存结果
    results_df = pd.DataFrame(results)
    results_df.to_csv(f"{OUTPUT_DIR}/pead_sensitivity.csv", index=False, encoding="utf-8-sig")
    
    print("\n" + "=" * 60)
    print("敏感性分析结果")
    print("=" * 60)
    print(results_df.to_string(index=False))
    
    print(f"\n结果已保存: {OUTPUT_DIR}/pead_sensitivity.csv")


if __name__ == "__main__":
    main()
