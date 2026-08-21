# coding: utf-8
"""清仓工具：查询持仓 → 排除保留股票 → 对剩余逐只卖出。

用法：
  python qmt_clear.py --keep 001378.SZ,002912.SZ              # dry-run 预览清仓计划
  python qmt_clear.py --keep 001378.SZ,002912.SZ --live        # 实盘卖出（模拟盘）
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
    ap.add_argument("--keep", default="", help="保留不卖出的股票(逗号分隔)")
    ap.add_argument("--live", action="store_true", help="实际卖出(默认 dry-run)")
    args = ap.parse_args()

    from xtquant import xttrader, xttype, xtconstant

    keep = set(c.strip() for c in args.keep.split(",") if c.strip())

    print("[1/4] 连接 miniQMT 并查询持仓 ...")
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    res = trader.connect()
    if res != 0:
        print(f"    !! 连接失败 code={res}")
        return
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    # xtquant 查询为异步回调，等待初始化
    time.sleep(10)
    positions = trader.query_stock_positions(account)
    if not positions:
        print("    !! 未查询到持仓（可能查询超时，可稍后重试）")
        trader.stop()
        return
    print(f"    持仓 {len(positions)} 只")

    print("[2/4] 生成清仓计划 ...")
    to_sell = []
    for p in positions:
        code = getattr(p, "stock_code", "")
        vol = int(getattr(p, "can_use_volume", 0) or getattr(p, "volume", 0))
        if vol <= 0:
            continue
        if code in keep:
            print(f"    保留 {code} (持仓 {vol})")
            continue
        to_sell.append({"code": code, "vol": vol, "cost": getattr(p, "open_price", 0)})
    if not to_sell:
        print("    !! 无需要清仓的持仓")
        trader.stop()
        return
    print(f"    待清仓 {len(to_sell)} 只：")
    for s in to_sell:
        print(f"      {s['code']}  可卖 {s['vol']} 股  成本 {s['cost']:.2f}")

    if not args.live:
        print("\n[3/4] DRY-RUN：未卖出任何委托。确认后用 --live 执行清仓。")
        trader.stop()
        return

    print("[3/4] 执行清仓卖出 ...")
    log_rows = []
    for s in to_sell:
        order_id = trader.order_stock(
            account, s["code"], xtconstant.STOCK_SELL, s["vol"],
            xtconstant.LATEST_PRICE, 0.0, "traework_clear", "clear_all_except_keep",
        )
        print(f"    卖出 {s['code']} {s['vol']} 股 -> order_id={order_id}")
        log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), s["code"], "SELL", s["vol"], 0.0, 0.0, order_id])

    print("[4/4] 记录清仓委托 ...")
    if log_rows:
        os.makedirs(os.path.dirname(C.TRADE_LOG), exist_ok=True)
        with open(C.TRADE_LOG, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if not os.path.exists(C.TRADE_LOG) or os.path.getsize(C.TRADE_LOG) == 0:
                w.writerow(["time", "code", "side", "vol", "price", "score", "order_id"])
            w.writerows(log_rows)
        print("    清仓委托已记录 ->", C.TRADE_LOG)
    trader.stop()
    print("    ✅ 清仓完成（模拟盘）")


if __name__ == "__main__":
    main()
