# coding=utf-8
"""反查清理委托状态（查询 order_stock 提交的 69 个卖出委托）"""
import sys
import time

ACCOUNT_ID = "70180771"


def _log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg))


def main():
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount

    class _CB(XtQuantTraderCallback):
        def on_disconnected(self):
            _log("disconnected")

        def on_stock_trade(self, t):
            _log("trade: %s" % str(t))

        def on_order_error(self, e):
            _log("order error: %s" % str(e))

    trader = XtQuantTrader(r"E:\国金QMT交易端模拟\userdata_mini", int(time.time()))
    trader.register_callback(_CB())
    trader.start()
    rc = trader.connect()
    if rc != 0:
        _log("连接失败 rc=%s" % rc)
        sys.exit(1)
    acc = StockAccount(ACCOUNT_ID)
    trader.subscribe(acc)
    _log("连接成功")

    orders = trader.query_stock_orders(acc)
    _log("当日委托总数: %d" % len(orders))
    n = 0
    for o in sorted(orders, key=lambda x: -(getattr(x, "order_time", 0) or 0)):
        code = getattr(o, "stock_code", "") or ""
        oid = getattr(o, "order_id", "") or ""
        vol = getattr(o, "order_volume", 0) or 0
        traded = getattr(o, "traded_volume", 0) or 0
        status = getattr(o, "order_status", "") or ""
        price = getattr(o, "price", 0) or 0
        remark = getattr(o, "order_remark", "") or ""
        if "cleanup_v2" in remark or "V2" in remark:
            n += 1
            _log("  %s oid=%s vol=%d 成交=%d status=%s price=%s remark=%s" % (
                code, oid, vol, traded, status, price, remark))
    _log("V2清理委托数: %d" % n)

    pos = trader.query_stock_positions(acc)
    _log("当前持仓: %d 只" % len(pos))


if __name__ == "__main__":
    main()
