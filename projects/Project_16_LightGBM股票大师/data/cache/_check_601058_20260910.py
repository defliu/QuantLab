# -*- coding: utf-8 -*-
import sys, os, time, urllib.request
sys.path.insert(0, os.getcwd())
import qmt_config as C
sys.path.append(C.XTPACK)

c = urllib.request.urlopen("http://qt.gtimg.cn/q=sh601058", timeout=10).read().decode("gbk")
f = c.split('"')[1].split("~")
print("601058 %s 现价=%s 昨收=%s 今开=%s 涨跌%%=%s 卖1=%s 卖1量=%s 买1=%s 时间=%s" % (
    f[1], f[3], f[4], f[5], f[32], f[19], f[20], f[9], f[30]))

try:
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    if trader.connect() == 0:
        acct = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(acct)
        time.sleep(3)
        orders = trader.query_stock_orders(acct)
        live = []
        if orders:
            for o in orders:
                status = int(getattr(o, "order_status", -1))
                if status in (50, 51, 52, 55):
                    live.append({"order_id": getattr(o, "order_id", ""),
                                 "code": getattr(o, "stock_code", ""),
                                 "status": status,
                                 "vol": getattr(o, "order_volume", 0),
                                 "traded": getattr(o, "traded_volume", 0),
                                 "price": getattr(o, "price", 0)})
        if live:
            print("OPEN_ORDERS:", live)
        else:
            print("OPEN_ORDERS: none")
        trader.stop()
    else:
        print("QMT_CONNECT_FAIL")
except Exception as e:
    print("QMT_QUERY_ERR: %r" % e)
