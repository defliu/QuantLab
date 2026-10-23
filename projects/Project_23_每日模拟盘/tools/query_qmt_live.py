# -*- coding: utf-8 -*-
"""用 miniQMT（xtquant）查询 67014907 的真实账户状态。

用途：为 V1.3 纸面 vs 实盘的差异归因提供权威基准。
只读查询，不下单、不撤单。
"""
import sys

sys.path.insert(0, r"D:\国金QMT交易端模拟\bin.x64\Lib\site-packages")

from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback  # noqa: E402
from xtquant.xttype import StockAccount                             # noqa: E402


QMT_PATH = r"D:\国金QMT交易端模拟\userdata_mini"
ACCOUNT = "67014907"


class _Cb(XtQuantTraderCallback):
    def on_disconnected(self):
        print("  [cb] disconnected")


def main():
    trader = XtQuantTrader(QMT_PATH, 20261022)
    trader.register_callback(_Cb())
    trader.start()
    r = trader.connect()
    print("connect:", r, "(0=ok)")
    if r != 0:
        print("!! 连接失败，检查 QMT 客户端是否登录")
        return 1

    acc = StockAccount(ACCOUNT)
    sub = trader.subscribe(acc)
    print("subscribe:", sub, "(0=ok)")

    asset = trader.query_stock_asset(acc)
    print("\n=== 资产 ===")
    if asset is None:
        print("  query_stock_asset 返回 None")
    else:
        for f in ("total_asset", "cash", "frozen_cash", "market_value"):
            print("  %-14s %s" % (f, getattr(asset, f, "N/A")))

    pos = trader.query_stock_positions(acc) or []
    print("\n=== 持仓 (%d 只) ===" % len(pos))
    print("  %-12s %8s %8s %10s %12s" % ("code", "volume", "can_use", "open_price", "market_value"))
    tot_mv = 0.0
    for p in pos:
        mv = getattr(p, "market_value", 0) or 0
        tot_mv += mv
        print("  %-12s %8s %8s %10s %12s" % (
            p.stock_code, p.volume, getattr(p, "can_use_volume", "-"),
            round(getattr(p, "open_price", 0) or 0, 3), round(mv, 2)))
    print("  持仓市值合计: %.2f" % tot_mv)

    orders = trader.query_stock_orders(acc) or []
    print("\n=== 当日委托 (%d 笔) ===" % len(orders))
    for o in orders[:20]:
        print("  %s %s vol=%s status=%s" % (
            o.stock_code, getattr(o, "order_type", "-"), o.order_volume,
            getattr(o, "order_status", "-")))

    trades = trader.query_stock_trades(acc) or []
    print("\n=== 当日成交 (%d 笔) ===" % len(trades))
    for t in trades[:20]:
        print("  %s %s vol=%s price=%s" % (
            t.stock_code, getattr(t, "order_type", "-"), t.traded_volume, t.traded_price))

    trader.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
