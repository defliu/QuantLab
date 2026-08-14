# coding: utf-8
"""检查 benchmark_index.duckdb 结构。"""
import duckdb

con = duckdb.connect("F:/backtest_workspace/data/duckdb/benchmark_index.duckdb")
try:
    tables = con.execute("SHOW TABLES").fetchdf()
    print("=== 表 ===")
    print(tables.to_string())
    for t in tables.iloc[:, 0].tolist():
        print("\n=== 表 %s schema ===" % t)
        try:
            print(con.execute("DESCRIBE %s" % t).fetchdf().to_string())
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            print("行数: %d" % n)
            print(con.execute("SELECT * FROM %s LIMIT 3" % t).fetchdf().to_string())
        except Exception as e:
            print("err: %s" % e)
except Exception as e:
    print("err: %s" % e)
con.close()
