# coding: utf-8
"""检查 astock stock_basic.parquet 是否含行业/板块信息，用于板块 RPS 实现。"""
import pyarrow.parquet as pq

PATH = "E:/astock/basic/stock_basic.parquet"
pf = pq.ParquetFile(PATH)
schema = pf.schema_arrow

print("=== stock_basic.parquet schema ===")
for field in schema:
    print("  %s: %s" % (field.name, field.type))

print("\n=== 样本数据 ===")
t = pq.read_table(PATH)
df = t.to_pandas()
print("行数: %d" % len(df))
print(df.head(3).to_string())
