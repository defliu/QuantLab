# coding: utf-8
"""当日委托/成交归档（防 301536.SZ 事件重演）。

背景：QMT `query_stock_orders` / `query_stock_trades` **只保留当日**，隔日永久查不到。
2026-10-21 301536.SZ 800 股卖出记录因此无法追溯，账本与账户长期不一致。

本脚本每天收盘后把当日全账号委托/成交 + 收盘持仓落盘，保留 ≥1 年。

重要：`query_stock_trades` 在 67014907 上实测会 **hang**（2026-10-22 V2 清仓时踩到），
故成交口径优先用 `query_stock_orders` 的 traded_volume/traded_price 还原，
`query_stock_trades` 用守护线程 + 超时兜底（拿不到就跳过，不阻塞）。

用法：
  python archive_daily_orders.py                 # 今日
  python archive_daily_orders.py --date 20261022 # 指定日（仅用于补跑当天的数据，隔日查不到）
产物：
  data/archive/orders_<date>.csv     当日全账号委托
  data/archive/trades_<date>.csv     当日全账号成交（orders 口径还原）
  data/archive/positions_<date>.json 收盘持仓快照 + 资产
"""
import argparse
import csv
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, "data", "archive")
QMT_USERDATA = r"D:\国金QMT交易端模拟\userdata_mini"
ACCOUNT_ID = "67014907"
TRADES_QUERY_TIMEOUT = 10


def _connect(account_id):
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(QMT_USERDATA, int(time.time()))
    trader.start()
    rc = trader.connect()
    if rc != 0:
        raise RuntimeError("连接 miniQMT 失败 rc=%s" % rc)
    account = xttype.StockAccount(account_id)
    sub = trader.subscribe(account)
    if sub != 0:
        raise RuntimeError("subscribe 失败 rc=%s（账号 %s 可能未开通 miniQMT）" % (sub, account_id))
    time.sleep(2)
    return trader, account


def _query_trades_with_timeout(trader, account, timeout=TRADES_QUERY_TIMEOUT):
    """query_stock_trades 守护线程超时包裹（67014907 上会 hang，绝不阻塞归档）。"""
    box = []

    def _worker():
        try:
            box.append(trader.query_stock_trades(account))
        except Exception as e:
            box.append(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        print("  !! query_stock_trades 阻塞 >%ds，跳过（改用 orders 口径还原）" % timeout)
        return None
    v = box[0] if box else None
    return v if isinstance(v, list) else None


def _dump(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description="当日委托/成交/持仓归档")
    ap.add_argument("--date", default=time.strftime("%Y%m%d"))
    ap.add_argument("--account", default=ACCOUNT_ID)
    args = ap.parse_args()
    date = args.date

    print("== 归档 %s 账号 %s ==" % (date, args.account))
    try:
        trader, account = _connect(args.account)
    except Exception as e:
        print("!! 连接失败，归档中止: %r" % (e,))
        return 1

    try:
        orders = trader.query_stock_orders(account) or []
        trades_raw = _query_trades_with_timeout(trader, account)
        positions = [p for p in (trader.query_stock_positions(account) or [])
                     if getattr(p, "volume", 0) > 0]
        asset = trader.query_stock_asset(account)
    finally:
        try:
            trader.stop()
        except Exception:
            pass

    os.makedirs(ARCHIVE, exist_ok=True)

    # 1) 委托
    ord_rows = []
    for o in orders:
        ord_rows.append({
            "code": getattr(o, "stock_code", ""),
            "order_id": getattr(o, "order_id", ""),
            "order_type": getattr(o, "order_type", ""),
            "order_status": getattr(o, "order_status", ""),
            "order_volume": getattr(o, "order_volume", ""),
            "traded_volume": getattr(o, "traded_volume", ""),
            "traded_price": getattr(o, "traded_price", ""),
            "order_time": getattr(o, "order_time", ""),
            "remark": getattr(o, "order_remark", ""),
        })
    p_ord = os.path.join(ARCHIVE, "orders_%s.csv" % date)
    _dump(p_ord, ord_rows, ["code", "order_id", "order_type", "order_status",
                            "order_volume", "traded_volume", "traded_price",
                            "order_time", "remark"])
    print("[归档] %s（%d 条）" % (p_ord, len(ord_rows)))

    # 2) 成交（orders 口径优先；trades 接口可用时以它为准补方向）
    trd_rows = []
    if trades_raw:
        for t in trades_raw:
            vol = int(getattr(t, "traded_volume", 0) or 0)
            px = float(getattr(t, "traded_price", 0) or 0)
            if vol > 0 and px > 0:
                trd_rows.append({
                    "code": getattr(t, "stock_code", ""),
                    "order_id": getattr(t, "order_id", ""),
                    "order_type": getattr(t, "order_type", ""),
                    "vol": vol, "price": px, "amount": round(vol * px, 2),
                    "traded_time": getattr(t, "traded_time", ""),
                    "remark": getattr(t, "order_remark", ""),
                    "src": "query_stock_trades",
                })
    if not trd_rows:
        for o in orders:
            vol = int(getattr(o, "traded_volume", 0) or 0)
            px = float(getattr(o, "traded_price", 0) or 0)
            if vol > 0 and px > 0:
                trd_rows.append({
                    "code": getattr(o, "stock_code", ""),
                    "order_id": getattr(o, "order_id", ""),
                    "order_type": getattr(o, "order_type", ""),
                    "vol": vol, "price": px, "amount": round(vol * px, 2),
                    "traded_time": getattr(o, "order_time", ""),
                    "remark": getattr(o, "order_remark", ""),
                    "src": "orders_fallback",
                })
    p_trd = os.path.join(ARCHIVE, "trades_%s.csv" % date)
    _dump(p_trd, trd_rows, ["code", "order_id", "order_type", "vol", "price",
                            "amount", "traded_time", "remark", "src"])
    print("[归档] %s（%d 条）" % (p_trd, len(trd_rows)))

    # 3) 持仓 + 资产
    pos_rows = [{
        "code": getattr(p, "stock_code", ""),
        "vol": int(getattr(p, "volume", 0) or 0),
        "can_use": int(getattr(p, "can_use_volume", 0) or 0),
        "open_price": round(float(getattr(p, "open_price", 0) or 0), 4),
        "market_value": round(float(getattr(p, "market_value", 0) or 0), 2),
    } for p in positions]
    p_pos = os.path.join(ARCHIVE, "positions_%s.json" % date)
    with open(p_pos, "w", encoding="utf-8") as f:
        json.dump({
            "date": date, "account_id": args.account,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(pos_rows), "positions": pos_rows,
            "total_asset": round(asset.total_asset, 2) if asset else None,
            "cash": round(asset.cash, 2) if asset else None,
            "market_value": round(asset.market_value, 2) if asset else None,
        }, f, ensure_ascii=False, indent=2)
    print("[归档] %s（%d 只）" % (p_pos, len(pos_rows)))
    print("[DONE] 归档完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
