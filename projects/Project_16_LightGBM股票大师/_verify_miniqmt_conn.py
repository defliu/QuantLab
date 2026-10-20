# coding: utf-8
"""miniQMT 只读连通性验证（不下单，无副作用）。

连接 67014907 → 交易通道（资产/持仓/委托/成交）+ 行情通道，逐步骤带耗时，
query_stock_trades 用线程超时保护（盘后可能挂起），便于定位卡点。
用法（miniqmt venv）：
  python _verify_miniqmt_conn.py
"""
import sys
import threading
import time

XTPACK = r"D:\国金QMT交易端模拟\bin.x64\Lib\site-packages"
USERDATA = r"D:\国金QMT交易端模拟\userdata_mini"
ACCOUNT_ID = "67014907"

sys.path.append(XTPACK)  # 放所有 import 末尾，让本环境 numpy 优先

from xtquant import xttrader, xttype  # noqa: E402


def call_with_timeout(fn, timeout=10):
    """返回 (ok, value_or_None, elapsed)。超时返回 (False, None, timeout)。"""
    box = {}
    t = threading.Thread(target=lambda: box.__setitem__("v", fn()), daemon=True)
    t0 = time.time()
    t.start()
    t.join(timeout)
    if t.is_alive():
        return False, None, time.time() - t0
    return True, box.get("v"), time.time() - t0


print("[1] import xtquant OK", flush=True)

trader = xttrader.XtQuantTrader(USERDATA, int(time.time()))
trader.start()
print("[2] trader.start() OK", flush=True)

t0 = time.time()
res = trader.connect()
print(f"[3] connect() = {res}   ({time.time() - t0:.1f}s)", flush=True)
if res != 0:
    print("CONNECT_FAIL code=%s" % res, flush=True)
    sys.exit(2)

acct = xttype.StockAccount(ACCOUNT_ID)
trader.subscribe(acct)
print(f"[4] subscribe({ACCOUNT_ID}) OK", flush=True)
time.sleep(3)

ok, asset, el = call_with_timeout(lambda: trader.query_stock_asset(acct))
if ok and asset is not None:
    print(
        "[5] asset total=%.2f cash=%.2f mv=%.2f frozen=%.2f  (%.1fs)"
        % (asset.total_asset, asset.cash, asset.market_value, asset.frozen_cash, el),
        flush=True,
    )
else:
    print(f"[5] query_stock_asset FAIL ok={ok} ({el:.1f}s)", flush=True)

ok, pos, el = call_with_timeout(lambda: trader.query_stock_positions(acct))
pos = pos or []
active = [p for p in pos if getattr(p, "volume", 0) > 0]
print(f"[6] positions total={len(pos)} active={len(active)}  (ok={ok}, {el:.1f}s)", flush=True)

ok, orders, el = call_with_timeout(lambda: trader.query_stock_orders(acct))
orders = orders or []
print(f"[7] orders today={len(orders)}  (ok={ok}, {el:.1f}s)", flush=True)

ok, trades, el = call_with_timeout(lambda: trader.query_stock_trades(acct), timeout=12)
if ok:
    print(f"[8] trades today={len(trades or [])}  ({el:.1f}s)", flush=True)
else:
    print(f"[8] query_stock_trades TIMEOUT >{el:.0f}s —— 盘后挂起（API 级问题）", flush=True)

# 行情通道 xtdata
try:
    from xtquant import xtdata  # noqa: E402

    probe = active[0].stock_code if active else "000001.SZ"
    xtdata.subscribe_quote(probe, period="tick", count=-1)
    time.sleep(1)
    tick = xtdata.get_full_tick([probe]).get(probe)
    last = float(tick.get("lastPrice", 0)) if tick else 0
    print(f"[9] xtdata {probe} lastPrice={last}", flush=True)
except Exception as e:
    print(f"[9] xtdata FAIL: {e}", flush=True)

trader.stop()
print("[10] trader.stop() OK  =>  MINIQMT_TRADE_CHANNEL_OK", flush=True)
