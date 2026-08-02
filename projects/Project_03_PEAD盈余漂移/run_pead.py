# coding=utf-8
"""PEAD策略回测 - 极速版"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
FINANCE_PATH = "E:/astock/finance/fina_indicator.parquet"
START_DATE = "2020-01-01"
END_DATE = "2026-06-30"
INITIAL_CAPITAL = 1_000_000
SUE_THRESHOLD = 0.30
MAX_3DAY_RETURN = 0.10
MAX_STOCKS = 30
HOLDING_PERIOD = 60
STOP_LOSS = -0.08
COMMISSION = 0.00025
STAMP_TAX = 0.001
SLIPPAGE = 0.002
OUTPUT_DIR = "E:/QuantLab/projects/Project_03_PEAD盈余漂移/results"


def main():
    print("=" * 50)
    print("PEAD盈余漂移策略回测")
    print("=" * 50)
    
    # 加载数据
    print("\n[1/3] 加载数据...")
    daily = pd.read_parquet(DAILY_PATH)
    
    # 过滤日期和去ST
    idx = daily.index
    trade_dates = idx.get_level_values("trade_date")
    mask = (trade_dates >= pd.Timestamp(START_DATE).date()) & (trade_dates <= pd.Timestamp(END_DATE).date())
    daily = daily.loc[mask].copy()
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    daily = daily.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])].copy()
    print(f"  行情: {len(daily)} 行, {daily.index.get_level_values('ts_code').nunique()} 只股票")
    
    # 加载财务数据
    print("[2/3] 加载财务数据...")
    fin = pd.read_parquet(FINANCE_PATH)
    fin = fin[["ts_code", "end_date", "ann_date", "netprofit_yoy", "or_yoy", "roe"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], format="%Y%m%d", errors="coerce")
    fin["end_date"] = pd.to_datetime(fin["end_date"], format="%Y%m%d", errors="coerce")
    fin = fin.dropna(subset=["ann_date", "end_date"])
    
    # 过滤日期范围
    start_ts = pd.Timestamp(START_DATE)
    end_ts = pd.Timestamp(END_DATE) + pd.Timedelta(days=90)
    fin = fin[(fin["ann_date"] >= start_ts) & (fin["ann_date"] <= end_ts)]
    
    # 过滤SUE
    fin = fin[~fin["netprofit_yoy"].isna()].copy()
    fin["sue"] = fin["netprofit_yoy"] / 100.0
    fin = fin[fin["sue"] >= SUE_THRESHOLD]
    
    # 过滤营收增长
    fin = fin[~fin["or_yoy"].isna() & (fin["or_yoy"] > 0)]
    
    print(f"  财务: {len(fin)} 条有效信号")
    
    # 构建价格面板
    print("[3/3] 执行回测...")
    close = daily["close"].unstack("ts_code")
    trade_dates_list = sorted(close.index.tolist())
    
    # 构建公告日->股票映射
    ann_map = {}  # date -> [(code, sue)]
    for _, row in fin.iterrows():
        ann_date = row["ann_date"].date()
        if ann_date not in ann_map:
            ann_map[ann_date] = []
        ann_map[ann_date].append((row["ts_code"], row["sue"]))
    
    # 回测
    capital = INITIAL_CAPITAL
    positions = {}  # code -> {shares, entry_price, entry_idx, sue}
    equity_curve = []
    trades = []
    
    for i, date in enumerate(trade_dates_list):
        if i % 200 == 0:
            print(f"  {i}/{len(trade_dates_list)} ({date})")
        
        # 卖出检查
        codes_to_sell = []
        for code, pos in list(positions.items()):
            hold_days = i - pos["entry_idx"]
            if hold_days >= HOLDING_PERIOD:
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
                                  "pnl": (sp/pos["entry_price"]-1)*100, "sue": pos["sue"]})
            del positions[code]
        
        # 买入新信号
        if date in ann_map and len(positions) < MAX_STOCKS:
            candidates = ann_map[date]
            # 检查3日涨幅
            for code, sue in candidates:
                if len(positions) >= MAX_STOCKS:
                    break
                if code in positions:
                    continue
                if code not in close.columns:
                    continue
                if date not in close.index:
                    continue
                
                bp = close.loc[date, code]
                if pd.isna(bp) or bp <= 0:
                    continue
                
                # 检查3日后涨幅
                future_idx = min(i + 3, len(trade_dates_list) - 1)
                future_date = trade_dates_list[future_idx]
                if future_date in close.index:
                    fp = close.loc[future_date, code]
                    if not pd.isna(fp) and fp > 0:
                        ret_3d = fp / bp - 1
                        if ret_3d > MAX_3DAY_RETURN:
                            continue
                
                alloc = capital / max(1, MAX_STOCKS - len(positions))
                alloc = min(alloc, capital * 0.95)
                if alloc < 10000:
                    continue
                shares = int(alloc / bp / 100) * 100
                if shares < 100:
                    continue
                
                capital -= shares * bp * (1 + COMMISSION + SLIPPAGE)
                positions[code] = {"shares": shares, "entry_price": bp, "entry_idx": i, "sue": sue}
                trades.append({"date": date, "code": code, "reason": f"SUE={sue:.2f}",
                              "pnl": 0, "sue": sue})
        
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
    eq.to_csv(f"{OUTPUT_DIR}/pead_equity.csv", index=False)
    if len(trades_df) > 0:
        trades_df.to_csv(f"{OUTPUT_DIR}/pead_trades.csv", index=False)
    
    with open(f"{OUTPUT_DIR}/pead_metrics.txt", "w", encoding="utf-8") as f:
        f.write(f"总收益: {total_ret:.1%}\n")
        f.write(f"年化收益: {ann_ret:.1%}\n")
        f.write(f"最大回撤: {max_dd:.1%}\n")
        f.write(f"夏普比率: {sharpe:.2f}\n")
        f.write(f"Calmar比率: {calmar:.2f}\n")
        f.write(f"胜率: {win_rate:.0%}\n")
        f.write(f"交易次数: {len(trades_df)}\n")
    
    print(f"\n结果: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
