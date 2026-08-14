# coding: utf-8
"""检查 stock_basic.parquet 的 industry 字段覆盖率。"""
import pyarrow.parquet as pq

PATH = "E:/astock/basic/stock_basic.parquet"
t = pq.read_table(PATH)
df = t.to_pandas()

print("总行数: %d" % len(df))
print("有 industry 的行数: %d (%.1f%%)" % (
    df["industry"].notna().sum(), df["industry"].notna().mean() * 100))

# 当前上市（list_status=L）且有行业的股票
active = df[(df["list_status"] == "L") & (df["industry"].notna())]
print("当前上市且有行业: %d" % len(active))

# 行业分布（前 20）
print("\n=== 行业分布（前 20）===")
print(active["industry"].value_counts().head(20).to_string())

# 样本
print("\n=== 样本（有行业的活跃股）===")
print(active[["ts_code", "name", "industry"]].head(10).to_string())
