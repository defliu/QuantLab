# coding: utf-8
"""miniQMT 全链路最小测试：下单 100 股 → 查委托 → 查成交 → 记录。

用于验证 TraeWork → miniQMT → 柜台的完整交易链路（模拟盘安全）。

用法：
  python qmt_mini_test.py                          # 默认 603969.SH 100股
  python qmt_mini_test.py --code 000001.SZ --vol 100
"""
import argparse
import csv
import os
import sys
import time

import qmt_config as C

sys.path.append(C.XTPACK)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="603969.SH", help="测试标的")
    ap.add_argument("--vol", type=int, default=100, help="股数(1手起)")
    args = ap.parse_args()

    from xtquant import xtdata, xttrader, xtconstant, xttype

    # 1) 取现价
    print(f"[1/5] 获取 {args.code} 现价 ...")
    xtdata.subscribe_quote(args.code, period="tick", count=-1)
    time.sleep(0.5)
    tick = xtdata.get_full_tick([args.code]).get(args.code)
    if not tick or not tick.get("lastPrice"):
        print("    !! 取价失败，miniQMT 客户端是否已启动登录？")
        return
    price = float(tick["lastPrice"])
    print(f"    现价 {price:.2f}")

    # 2) 连接交易
    print("[2/5] 连接 miniQMT 交易通道 ...")
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    res = trader.connect()
    if res != 0:
        print(f"    !! 连接失败 code={res}")
        return
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    print(f"    已连接账号 {C.ACCOUNT_ID}")

    # 3) 下单（限价=现价+1档，模拟盘更易成交）
    print(f"[3/5] 下单 {args.code} 买入 {args.vol} 股 @ {price:.2f} ...")
    order_price = round(price * 1.005, 2)
    order_id = trader.order_stock(
        account, args.code, xtconstant.STOCK_BUY, args.vol,
        xtconstant.FIX_PRICE, order_price, "traework_mini_test", "full_link_test",
    )
    print(f"    order_id = {order_id}")
    if order_id < 0:
        print(f"    !! 下单被拒 code={order_id}（可能非交易时段或参数问题）")
        trader.stop()
        return

    # 4) 查委托状态
    print("[4/5] 查询委托状态 ...")
    time.sleep(2)
    orders = trader.query_stock_orders(account)
    hit = None
    for o in orders or []:
        if getattr(o, "order_id", None) == order_id:
            hit = o
            break
    if hit:
        status = getattr(hit, "order_status", "?")
        status_map = {48: "未报", 49: "待报", 50: "已报", 51: "已报待撤", 52: "部成待撤",
                      53: "部撤", 54: "已撤", 55: "部成", 56: "已成", 57: "废单", 255: "未知"}
        print(f"    委托状态码 = {status}（{status_map.get(status, '?')}）")
        print(f"    委托价 {getattr(hit,'price',0):.2f}  数量 {getattr(hit,'order_volume',0)}")
        print(f"    提示：57=废单，常见原因=非交易时段/涨跌停限价/资金不足，请核对")
    else:
        print("    未在委托列表找到，稍后再查（模拟盘撮合可能有延迟）")

    # 5) 查成交并记录
    print("[5/5] 查询成交 ...")
    time.sleep(2)
    trades = trader.query_stock_trades(account) or []
    matched = [t for t in trades if getattr(t, "order_id", None) == order_id]
    if matched:
        for t in matched:
            print(f"    成交: {args.code} {getattr(t,'traded_volume',0)}股 @ {getattr(t,'traded_price',0):.2f}")
        with open(C.TRADE_LOG, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if not os.path.exists(C.TRADE_LOG) or os.path.getsize(C.TRADE_LOG) == 0:
                w.writerow(["time", "code", "side", "vol", "price", "score", "order_id"])
            for t in matched:
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), args.code, "BUY",
                            getattr(t, "traded_volume", args.vol), getattr(t, "traded_price", price),
                            "MINI_TEST", order_id])
        print(f"    成交已写入 {C.TRADE_LOG}")
    else:
        print("    暂未查到成交（模拟盘可能需撮合或非交易时段，稍后查询）")

    trader.stop()
    print("    ✅ 全链路测试结束")


if __name__ == "__main__":
    main()
