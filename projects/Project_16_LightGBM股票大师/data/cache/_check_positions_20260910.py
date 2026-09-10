# -*- coding: utf-8 -*-
import sys, os, time
sys.path.insert(0, os.getcwd())
import qmt_config as C
sys.path.append(C.XTPACK)

try:
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    if trader.connect() == 0:
        acct = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(acct)
        time.sleep(3)
        pos = trader.query_stock_positions(acct)
        if pos:
            for p in pos:
                code = getattr(p, "stock_code", "")
                vol = int(getattr(p, "volume", 0) or 0)
                if vol > 0:
                    print("%s vol=%d can_use=%d open_price=%.3f market_value=%.0f" % (
                        code, vol, int(getattr(p, "can_use_volume", 0) or 0),
                        float(getattr(p, "open_price", 0) or 0),
                        float(getattr(p, "market_value", 0) or 0)))
        else:
            print("NO_POSITIONS")
        trader.stop()
    else:
        print("QMT_CONNECT_FAIL")
except Exception as e:
    print("QMT_ERR: %r" % e)
