# coding=utf-8
"""检查 astock stock_daily.parquet 实际结构。"""
import pyarrow.parquet as pq

pf = pq.ParquetFile("E:/astock/daily/stock_daily.parquet")
schema = pf.schema_arrow
print("=== parquet schema ===")
for f in schema:
    print("  %s: %s" % (f.name, f.type))
print("\n前 3 行：")
t = pq.read_table("E:/astock/daily/stock_daily.parquet").slice(0, 3)
print(t.to_pandas().to_string())
