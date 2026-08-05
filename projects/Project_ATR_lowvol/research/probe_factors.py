import duckdb
con = duckdb.connect()
print("=== fina_indicator 含 ROE 的列 ===")
cols = con.execute("DESCRIBE SELECT * FROM read_parquet('E:/astock/finance/fina_indicator.parquet')").fetchdf()
hits = [c for c in cols['column_name'] if any(k in c.lower() for k in ('roe','eps','profit','q_roe'))]
print(hits[:15])
print("=== 样本 ROE ===")
print(con.execute("SELECT ts_code, end_date, roe, q_roe FROM read_parquet('E:/astock/finance/fina_indicator.parquet') WHERE roe IS NOT NULL LIMIT 3").fetchdf().to_string())
print("=== stock_basic 全部列 ===")
print(con.execute("DESCRIBE SELECT * FROM read_parquet('E:/astock/basic/stock_basic.parquet')").fetchdf()['column_name'].tolist())
print("=== industry 取值 Top12 ===")
print(con.execute("SELECT industry, COUNT(*) c FROM read_parquet('E:/astock/basic/stock_basic.parquet') WHERE industry IS NOT NULL GROUP BY industry ORDER BY c DESC LIMIT 12").fetchdf().to_string())
print("=== daily 估值/市值列 ===")
print([c for c in con.execute("DESCRIBE SELECT * FROM read_parquet('E:/astock/daily/stock_daily.parquet')").fetchdf()['column_name'] if c in ('pe','pe_ttm','pb','circ_mv','total_mv')])
