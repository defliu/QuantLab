# coding=utf-8
"""撤销清理遗留的 7 笔周末挂单（status=50 已报待成交）
避免周一开盘在修复版策略运行前意外成交部分持仓，造成账实不一致。
仅撤销 oid 属于本次清理（remark 含 'V2脏仓清理'）且 status=50 的挂单。
"""
import time

from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount


class CB(XtQuantTraderCallback):
    def on_disconnected(self):
        pass

    def on_stock_trade(self, t):
        pass

    def on_order_error(self, e):
        pass


t = XtQuantTrader(r"E:\国金QMT交易端模拟\userdata_mini", int(time.time()))
t.register_callback(CB())
t.start()
rc = t.connect()
acc = StockAccount("67014907")
t.subscribe(acc)
orders = []
for _i in range(10):
    time.sleep(1)
    orders = t.query_stock_orders(acc)
    if orders:
        break
out = ["total orders: %d" % len(orders)]
pending = [o for o in orders
           if (getattr(o, "order_status", "") == 50)
           and "V2" in (getattr(o, "order_remark", "") or "")]
out.append("V2挂单(status=50): %d" % len(pending))
cancelled = 0
failed = 0
for o in pending:
    code = getattr(o, "stock_code", "")
    oid = getattr(o, "order_id", "")
    try:
        t.cancel_order_stock(acc, oid)
        out.append("  [撤] %s oid=%s" % (code, oid))
        cancelled += 1
    except Exception as e:
        out.append("  [失败] %s oid=%s: %s" % (code, oid, str(e)))
        failed += 1
out.append("撤销成功=%d 失败=%d" % (cancelled, failed))
with open(r"D:\QMT_POOL\cancel_pending_dump.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(out))
print("done")