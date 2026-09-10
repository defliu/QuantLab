# -*- coding: utf-8 -*-
"""查 67014907 账户当前真实持仓 + 可用资金（只读）。"""
import sys, time
sys.path.append(r"D:\国金QMT交易端模拟\bin.x64\Lib\site-packages")
from xtquant import xttrader, xttype

trader = xttrader.XtQuantTrader(r"D:\国金QMT交易端模拟\userdata_mini", int(time.time()))
trader.start()
try:
    if trader.connect() != 0:
        print("!! 连接失败"); sys.exit(1)
    acct = xttype.StockAccount("67014907")
    trader.subscribe(acct)
    time.sleep(3)
    print("--- 持仓 ---")
    pos = trader.query_stock_positions(acct)
    if not pos:
        print("无持仓")
    for p in pos:
        print({"code": getattr(p, "stock_code", "?"), "vol": getattr(p, "volume", "?"),
               "can_use": getattr(p, "can_use_volume", "?"), "open": getattr(p, "open_price", "?")})
    print("--- 可用资金 ---")
    assets = trader.query_stock_asset(acct)
    if assets:
        print({"cash": getattr(assets, "cash", "?"), "mktval": getattr(assets, "market_value", "?"),
               "total": getattr(assets, "total_asset", "?")})
finally:
    trader.stop()
