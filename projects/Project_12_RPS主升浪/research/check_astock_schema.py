# coding: utf-8
"""检查 astock parquet 的字段名，确保策略代码匹配。"""
import pyarrow.parquet as pq

PARQUET_PATH = "E:/astock/daily/stock_daily.parquet"

# 读取 schema
pf = pq.ParquetFile(PARQUET_PATH)
schema = pf.schema_arrow

print("=== astock parquet schema ===")
for field in schema:
    print("  %s: %s" % (field.name, field.type))

print("\n=== 关键字段检查 ===")
field_names = [f.name for f in schema]
required = ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "is_st"]
for col in required:
    if col in field_names:
        print("  [OK] %s" % col)
    else:
        print("  [MISSING] %s" % col)

# 读取一行样本
print("\n=== 样本数据（第一行）===")
t = pq.read_table(PARQUET_PATH, columns=required)
df = t.to_pandas()
print(df.head(1).to_string())
