# coding: utf-8
"""诊断 ATR 回测 mask：逐条件统计通过数，定位过滤过狠的环节。"""
import duckdb, pandas as pd, numpy as np

PARQUET = "E:/astock/daily/stock_daily.parquet"
con = duckdb.connect()
df = con.execute(f"""
    SELECT ts_code, CAST(trade_date AS DATE) AS date,
           open, high, low, close, vol, amount, turnover_rate, adj_factor, is_st,
           COALESCE(suspend_type,'') AS suspend_type
    FROM read_parquet('{PARQUET}')
    WHERE CAST(trade_date AS DATE) >= DATE '2022-01-01'
""").fetchdf()
con.close()
for c in ["open","high","low","close"]:
    df["adj_"+c] = df[c] * df["adj_factor"]
df["is_st"] = (df["is_st"] == 1.0)
df["suspended"] = df["suspend_type"].astype(str).str.strip() != ""
df["ts_code"] = df["ts_code"].astype("category")
df = df.sort_values(["ts_code","date"]).reset_index(drop=True)
g = df.groupby("ts_code", sort=False)
df["prev_close"] = g["adj_close"].shift(1)
df["tr1"] = df["adj_high"] - df["adj_low"]
df["tr2"] = (df["adj_high"] - df["prev_close"]).abs()
df["tr3"] = (df["adj_low"]  - df["prev_close"]).abs()
df["tr"]  = df[["tr1","tr2","tr3"]].max(axis=1)
df["atr14"] = g["tr"].transform(lambda s: s.rolling(14, min_periods=14).mean())
df["atr_pct"] = df["atr14"] / df["adj_close"] * 100
df["amt5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=5).sum())
df["bar_idx"] = g.cumcount()

print("=== 全样本(2022起) atr_pct 分布 ===")
print(df["atr_pct"].describe())
print("atr_pct<6 占比: %.2f%%" % (100*(df["atr_pct"]<6).mean()))
print("atr_pct 在(0,6): %.2f%%" % (100*((df["atr_pct"]>0)&(df["atr_pct"]<6)).mean()))
print("atr_pct NaN: %d" % df["atr_pct"].isna().sum())

bt = df[df["date"] >= pd.Timestamp("2023-01-01")].copy()
print("\n=== 回测区间 bt 行数: %d ===" % len(bt))
print("bt atr_pct<6        : %d" % (bt["atr_pct"]<6).sum())
print("bt turn [1,8]       : %d" % ((bt["turnover_rate"]>=1)&(bt["turnover_rate"]<=8)).sum())
print("bt amt5>0           : %d" % (bt["amt5"]>0).sum())
print("bt not st           : %d" % (~bt["is_st"]).sum())
print("bt not suspended    : %d" % (~bt["suspended"]).sum())
print("bt bar_idx>=60      : %d" % (bt["bar_idx"]>=60).sum())
print("bt atr_pct NaN      : %d" % bt["atr_pct"].isna().sum())
print("bt turnover NaN     : %d" % bt["turnover_rate"].isna().sum())

mask = (bt["atr_pct"]<6)&(bt["turnover_rate"]>=1)&(bt["turnover_rate"]<=8)&(bt["amt5"]>0)&(~bt["is_st"])&(~bt["suspended"])&(bt["bar_idx"]>=60)
print("\n=== 全条件 mask 通过: %d ===" % mask.sum())
cand = bt[mask]
print("有候选的交易日数: %d / %d" % (cand["date"].nunique(), bt["date"].nunique()))
print("每日候选数(前10天):")
print(cand.groupby("date").size().head(10).to_string())
