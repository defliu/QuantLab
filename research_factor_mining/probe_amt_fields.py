# coding=utf-8
"""数据可用性探查：确认 ATR 策略侧 market_window 是否含 amount/circ_mv 可算 amt_ratio。
只读探查，不回测。"""
import sys
sys.path.insert(0, "D:/QuantLab")
from data.astock_reader import AstockParquetReader
import pandas as pd

reader = AstockParquetReader("D:/astock/daily/stock_daily.parquet", adjustment="hfq")
codes = ["000001.SZ", "600519.SH", "002626.SZ"]
win = reader.load_window(codes, "2026-01-01", "2026-03-01")
reader.close()
for c in codes:
    df = win[c]
    print(c, "cols:", list(df.columns))
    has_amt = "amount" in df.columns
    has_mv = "circ_mv" in df.columns
    if has_amt and has_mv:
        amt = df["amount"].astype(float).tail(20)
        mv = float(df["circ_mv"].astype(float).iloc[-1])
        ratio = float(amt.mean()) / mv if mv > 0 else None
        last_to = df["turnover_rate"].astype(float).iloc[-1]
        print("  amount_ma20=%s circ_mv=%s amt_ratio=%.6f last_turnover=%.4f%%"
              % (amt.mean(), mv, ratio if ratio else -1, last_to))
    else:
        print("  MISSING amount=%s circ_mv=%s" % (has_amt, has_mv))
