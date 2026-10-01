# coding: utf-8
"""只读核查：查 67014907 当日委托与成交明细，用于人工对账工件修复。

严格只读：仅调用 query_stock_trades / query_stock_orders / query_stock_positions，
绝不调用 order_stock / cancel_order_stock。任何异常只记录不抛出。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qmt_config as C

sys.path.append(C.XTPACK)

ACCOUNT_ID = C.ACCOUNT_ID
CODES = ["603237.SH", "603049.SH", "000920.SZ", "603882.SH", "600547.SH", "000155.SZ", "300531.SZ"]

STATUS_NAME = {
    50: "已报", 51: "已报待撤", 52: "部成待撤", 53: "部撤",
    54: "已撤", 55: "部成", 56: "已成", 57: "废单", 255: "未知",
}


def main():
    from xtquant import xttrader, xttype

    out = {"account_id": ACCOUNT_ID, "trades": [], "orders": [], "positions": [], "errors": []}

    print("[step] starting trader...", flush=True)
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    print("[step] connecting...", flush=True)
    rc = trader.connect()
    print("[step] connect rc=%s" % rc, flush=True)
    if rc != 0:
        print(json.dumps({"fatal": "connect failed rc=%s" % rc}, ensure_ascii=False))
        return
    account = xttype.StockAccount(ACCOUNT_ID)
    trader.subscribe(account)
    print("[step] subscribed, settling...", flush=True)
    time.sleep(8)

    try:
        for t in trader.query_stock_trades(account) or []:
            code = getattr(t, "stock_code", "")
            if code in CODES:
                out["trades"].append({
                    "code": code,
                    "order_id": getattr(t, "order_id", 0),
                    "traded_volume": getattr(t, "traded_volume", 0),
                    "traded_price": getattr(t, "traded_price", 0.0),
                    "traded_time": getattr(t, "traded_time", 0),
                    "order_type": getattr(t, "order_type", 0),
                })
    except Exception as e:
        out["errors"].append("trades: %r" % (e,))

    try:
        for o in trader.query_stock_orders(account) or []:
            code = getattr(o, "stock_code", "")
            if code in CODES:
                st = int(getattr(o, "order_status", 255))
                out["orders"].append({
                    "code": code,
                    "order_id": getattr(o, "order_id", 0),
                    "order_volume": getattr(o, "order_volume", 0),
                    "traded_volume": getattr(o, "traded_volume", 0),
                    "order_price": getattr(o, "price", 0.0),
                    "order_status": st,
                    "status_text": STATUS_NAME.get(st, str(st)),
                    "order_time": getattr(o, "order_time", 0),
                    "order_type": getattr(o, "order_type", 0),
                    "remark": getattr(o, "remark", ""),
                })
    except Exception as e:
        out["errors"].append("orders: %r" % (e,))

    try:
        for p in trader.query_stock_positions(account) or []:
            code = getattr(p, "stock_code", "")
            if code in CODES:
                out["positions"].append({
                    "code": code,
                    "volume": getattr(p, "volume", 0),
                    "can_use_volume": getattr(p, "can_use_volume", 0),
                    "open_price": getattr(p, "open_price", 0.0),
                })
    except Exception as e:
        out["errors"].append("positions: %r" % (e,))

    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()