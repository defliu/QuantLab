# coding: utf-8
"""纯因子基准: 隔离 ATR低波因子本身质量(不含策略的选股/仓位/退出逻辑)。
每天收盘时, 等权买入"当日所有合格候选"(mask), 次日收盘卖出, 复利。
不做任何止损/止盈/条件失效/仓位限制 —— 纯粹看因子的选股信号是否自带正向收益。
"""
import time, numpy as np, pandas as pd, duckdb
PARQUET = "E:/astock/daily/stock_daily.parquet"
ATR_THRESHOLD=6.0; MIN_TURNOVER=1.0; MAX_TURNOVER=8.0; MIN_BARS=60
WARMUP_START="2022-01-01"

t0=time.time()
con=duckdb.connect()
df=con.execute(f"""
  SELECT ts_code, CAST(trade_date AS DATE) AS date, open, high, low, close, vol, amount,
         turnover_rate, adj_factor, is_st
  FROM read_parquet('{PARQUET}')
  WHERE CAST(trade_date AS DATE) >= DATE '{WARMUP_START}'
""").fetchdf()
print("[1] 加载 %d 行 耗时 %.1fs"%(len(df), time.time()-t0))

df["adj_close"]=df["close"]*df["adj_factor"]
df=df.sort_values(["ts_code","date"]).reset_index(drop=True)

# ATR(14) on adj_close
def atr(g):
    h, l, c = g["high"].values, g["low"].values, g["adj_close"].values
    pc = np.roll(c,1); pc[0]=np.nan
    tr = np.maximum(h-l, np.maximum(np.abs(h-pc), np.abs(l-pc)))
    return pd.Series(tr, index=g.index).rolling(14).mean()
df["atr"]=df.groupby("ts_code", group_keys=False).apply(atr, include_groups=False).reset_index(level=0, drop=True)
df["atr_pct"]=df["atr"]/df["adj_close"]*100
df["amt5"]=df.groupby("ts_code")["amount"].transform(lambda s: s.rolling(5,min_periods=1).mean())
df["bar_idx"]=df.groupby("ts_code").cumcount()
# 次日收盘收益(因子信号: 今日收盘买, 明日收盘卖)
df=df.sort_values(["ts_code","date"])
df["fwd_ret"]=df.groupby("ts_code")["adj_close"].pct_change().shift(-1)

mask = (df["atr_pct"]<ATR_THRESHOLD) & (df["turnover_rate"]>=MIN_TURNOVER) & (df["turnover_rate"]<=MAX_TURNOVER) \
       & (df["amt5"]>0) & (df["is_st"]==0) & (df["bar_idx"]>=MIN_BARS) & (df["vol"]>0) & (df["fwd_ret"].notna())
elig=df[mask]

# 每日等权因子收益
daily=elig.groupby("date")["fwd_ret"].mean().sort_index()
# 复利(假设每日全换仓, 无交易成本 = 因子原始质量上限)
nav= (1+daily).cumprod()
total_ret = nav.iloc[-1]-1
n_days=len(daily)
ann = (1+total_ret)**(252/n_days)-1
# 带单边 0.1% 再平衡成本(每日全换仓)
daily_cost = daily - 0.001
nav_c=(1+daily_cost).cumprod()
total_ret_c=nav_c.iloc[-1]-1
ann_c=(1+total_ret_c)**(252/n_days)-1
# 回撤
peak=nav.cummax(); dd=(nav/peak-1).min()

print("\n===== 纯因子基准(等权买入当日所有合格候选, 次日卖) =====")
print("  样本交易日: %d"%n_days)
print("  无成本 总收益: %.2f%%  年化: %.2f%%"%(total_ret*100, ann*100))
print("  单边0.1pct成本 总收益: %.2f%%  年化: %.2f%%"%(total_ret_c*100, ann_c*100))
print("  最大回撤: %.2f%%"% (dd*100))
print("  合格候选日均数: %.1f"% elig.groupby('date').size().mean())

# 因子年度拆解(无成本, 看信号本身的年度分布)
daily_ser = (1+daily).cumprod()
ann_ret = daily_ser.groupby(daily_ser.index.year).apply(lambda s: s.iloc[-1]/s.iloc[0]-1)
print("\n  因子年度收益(无成本, 等权每日换仓):")
for y, r in ann_ret.items():
    print("    %d: %+.2f%%"%(y, r*100))
print("  耗时 %.1fs"%(time.time()-t0))
