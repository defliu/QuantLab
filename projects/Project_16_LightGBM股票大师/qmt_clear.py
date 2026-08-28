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
    ap.add_argument("--auto-keep", action="store_true", help="自动从 qmt_trade_log 计算当前策略持仓作为保留名单")
    ap.add_argument("--live", action="store_true", help="实际卖出(默认 dry-run)")
    args = ap.parse_args()

    from xtquant import xttrader, xttype, xtconstant

    keep = set(c.strip() for c in args.keep.split(",") if c.strip())
    if args.auto_keep:
        rows = C.load_trade_log_rows()
        if rows:
            bought, sold = {}, {}
            for row in rows:
                code = (row.get("code") or "").strip()
                if not code:
                    continue
                try:
                    vol = int(float(row.get("vol", 0) or 0))
                except ValueError:
                    vol = 0
                side = row.get("side", "")
                if side == "BUY":
                    bought[code] = bought.get(code, 0) + vol
                elif side == "SELL":
                    sold[code] = sold.get(code, 0) + vol
            keep = {c for c, b in bought.items() if b - sold.get(c, 0) > 0}
            print(f"    [auto-keep] 当前策略持仓(保留): {sorted(keep) if keep else '无'}")
        else:
            print("    !! 未找到成交记录，auto-keep 无法计算策略持仓，将清空所有持仓")

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

    print("[3/4] 执行清仓卖出（委托守护：未成撤单重试，涨跌停跳过）...")
    import order_guard
    log_rows = []
    guard_note = []
    for s in to_sell:
        price = 0.0
        try:
            from xtquant import xtdata
            xtdata.subscribe_quote(s["code"], period="tick", count=-1)
            import time as _t
            _t.sleep(0.3)
            tick = xtdata.get_full_tick([s["code"]]).get(s["code"])
            if tick:
                price = float(tick.get("lastPrice", 0) or 0)
        except Exception:
            price = 0.0
        r = order_guard.order_with_guard(trader, account, s["code"], "SELL", s["vol"],
                                         price if price > 0 else None, "clear_all_except_keep")
        print(f"    卖出 {s['code']} {s['vol']} 股 -> {r['note']}")
        guard_note.append(f"{s['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), s["code"], "SELL",
                             r["traded_vol"], price, 0.0, r["order_id"]])
    if guard_note:
        print("  [委托守护] " + " | ".join(guard_note))

    print("[4/4] 记录清仓委托 ...")
    if log_rows:
        C.append_trade_rows(log_rows)
        print("    清仓委托已记录 ->", C.TRADE_LOG)
    trader.stop()
    print("    ✅ 清仓完成（模拟盘）")


if __name__ == "__main__":
    main()
