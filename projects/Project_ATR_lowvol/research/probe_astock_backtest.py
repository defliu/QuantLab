# coding: utf-8
"""探查 E:/astock/daily/stock_daily.parquet，确认回测可用字段与阈值合理性。"""
import duckdb

PARQUET = "E:/astock/daily/stock_daily.parquet"
con = duckdb.connect()

print("=== 1. SCHEMA ===")
schema = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{PARQUET}')").fetchdf()
print(schema.to_string())

print("\n=== 2. 日期范围 / 股票数 / 总行数 ===")
meta = con.execute(
    f"SELECT min(trade_date) mn, max(trade_date) mx, count(*) n, count(distinct ts_code) nc "
    f"FROM read_parquet('{PARQUET}')"
).fetchdf()
print(meta.to_string())

print("\n=== 3. turnover_rate 分布（全样本，确认 1-8% 阈值合理性）===")
to = con.execute(
    f"SELECT min(turnover_rate) mn, max(turnover_rate) mx, avg(turnover_rate) av, "
    f"quantile_cont(turnover_rate,0.1) p10, quantile_cont(turnover_rate,0.5) p50, "
    f"quantile_cont(turnover_rate,0.9) p90, quantile_cont(turnover_rate,0.99) p99 "
    f"FROM read_parquet('{PARQUET}') WHERE turnover_rate IS NOT NULL"
).fetchdf()
print(to.to_string())
print("turnover in [1,8] 占比:")
r = con.execute(
    f"SELECT 100.0*sum(CASE WHEN turnover_rate>=1 AND turnover_rate<=8 THEN 1 ELSE 0 END)/count(*) "
    f"FROM read_parquet('{PARQUET}') WHERE turnover_rate IS NOT NULL"
).fetchdf()
print(r.to_string())

print("\n=== 4. 样本行（600000.SH 最近3日，看关键字段）===")
samp = con.execute(
    f"SELECT ts_code, trade_date, open, high, low, close, vol, amount, "
    f"turnover_rate, pe, pe_ttm, pb, total_mv, circ_mv, adj_factor, is_st, up_limit, down_limit "
    f"FROM read_parquet('{PARQUET}') WHERE ts_code='600000.SH' ORDER BY trade_date DESC LIMIT 3"
).fetchdf()
print(samp.to_string())

print("\n=== 5. 2023起 日频截面天数（用于判断回测天数）===")
days = con.execute(
    f"SELECT count(distinct trade_date) n FROM read_parquet('{PARQUET}') WHERE trade_date>='2023-01-01'"
).fetchdf()
print(days.to_string())

con.close()
print("\nDONE")
