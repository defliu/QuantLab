# coding=utf-8
"""数据预检：确认大盘域 kill test 所需字段的可用性与规模（只读，不修改任何数据）"""
import time
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

DAILY_PATH = r"D:/astock/daily/stock_daily.parquet"

t0 = time.time()
pf = pq.ParquetFile(DAILY_PATH)
md = pf.metadata
print("== parquet metadata ==")
print("num_rows      :", md.num_rows)
print("num_row_groups:", md.num_row_groups)
print("num_columns   :", md.num_columns)
print("schema        :", [f.name for f in pf.schema_arrow])
print("read_meta     : %.1fs" % (time.time() - t0))

# 只加载需要的列
need = ["trade_date", "ts_code", "open", "high", "low", "close", "vol", "circ_mv"]
t1 = time.time()
df = pd.read_parquet(DAILY_PATH, columns=need).reset_index()
print("\n== loaded ==")
print("shape :", df.shape)
print("load  : %.1fs" % (time.time() - t1))
print("dtypes:\n", df.dtypes)
print("\nhead:\n", df.head(3))

# 日期范围
d = pd.to_datetime(df["trade_date"])
print("\n== date range ==")
print("min:", d.min(), " max:", d.max())
print("n_trade_dates:", d.nunique())
print("n_codes      :", df["ts_code"].nunique())

# 每年交易日数
yr = d.dt.year.value_counts().sort_index()
print("\ntrade days per year:")
print(yr.to_string())

# 关键列缺失率（按年）
print("\n== NaN rate by year (关键列) ==")
df["_y"] = d.dt.year
for c in ["open", "high", "low", "close", "vol", "circ_mv"]:
    g = df.groupby("_y")[c].apply(lambda s: s.isna().mean())
    print("%-8s %s" % (c, "  ".join("%d:%.3f" % (y, v) for y, v in g.items())))

# circ_mv 量级确认（单位应为万元）
print("\n== circ_mv 量级（确认单位=万元）==")
sub = df[(d >= "2024-01-01") & (d <= "2024-12-31")]
print("2024 circ_mv describe:")
print(sub["circ_mv"].describe())
top500_threshold = sub.groupby("trade_date")["circ_mv"].apply(
    lambda s: s.sort_values(ascending=False).head(500).min())
print("2024 每日 top500 门槛(万元) median=%.0f  约 %.1f 亿元" % (
    top500_threshold.median(), top500_threshold.median() / 10000.0))

# 每日股票数（判断宇宙是否充足）
print("\n== 每日股票数 ==")
cnt = df.groupby("trade_date")["ts_code"].nunique()
print("min=%d  median=%d  max=%d" % (cnt.min(), cnt.median(), cnt.max()))
low_days = cnt[cnt < 500]
print("不足500只的交易日数:", len(low_days))
if len(low_days):
    print("  最早:", str(low_days.index.min()) if len(low_days) else "-")

print("\nTOTAL %.1fs" % (time.time() - t0))
