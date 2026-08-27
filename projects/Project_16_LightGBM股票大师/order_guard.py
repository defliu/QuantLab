# coding: utf-8
"""委托守护：下单后轮询成交状态，未全部成交则撤单重试（更新价格），直到成交或涨跌停跳过。

核心逻辑（rebalance_daily / qmt_clear 等真实委托复用）：
  1. 下单 order_stock → order_id
  2. 轮询 query_stock_orders（间隔 3s，单次最长等 WAIT_SECONDS=60s，即"委托后一分钟查一下"）
  3. 全部成交（traded_volume >= order_volume）→ 返回成功
  4. 废单/已撤 → 若已触涨跌停则跳过(limit)；否则更新价格重新委托
  5. 单次等待超时仍未全成 → cancel_order_stock 撤单 → 更新价格重新委托
  6. 最多 MAX_RETRY 次；期间检测到涨跌停（买入涨停封板/卖出跌停封板）立即跳过
  7. 部分成交：撤单后仅重试剩余未成交部分

返回：
  {"ok": bool, "action": "FILLED"|"LIMIT_SKIP"|"CANCELED_TIMEOUT"|"REJECTED"|"ERROR",
   "traded_vol": int, "order_id": int, "attempts": int, "note": str}
"""
import sys
import time

import qmt_config as C

sys.path.append(C.XTPACK)


# 订单状态（xtconstant）
ORDER_REPORTED = 50       # 已报
ORDER_REPORTED_CANCEL = 51  # 已报待撤
ORDER_PARTSUCC_CANCEL = 52  # 部成待撤
ORDER_PART_CANCEL = 53    # 部撤
ORDER_CANCELED = 54       # 已撤
ORDER_PART_SUCC = 55      # 部成
ORDER_SUCCEEDED = 56      # 已成
ORDER_JUNK = 57           # 废单

WAIT_SECONDS = 60          # 单次委托最长等待（秒）
POLL_INTERVAL = 3          # 轮询间隔（秒）
MAX_RETRY = 5              # 最大撤单重试次数


def get_limit_prices(code):
    """取涨跌停价 (up, down)。失败返回 None。"""
    try:
        from xtquant import xtdata
        d = xtdata.get_instrument_detail(code)
        if d:
            up = float(d.get("UpStopPrice", 0) or 0)
            down = float(d.get("DownStopPrice", 0) or 0)
            if up > 0 and down > 0:
                return up, down
    except Exception as e:
        print(f"    !! {code} 涨跌停价查询异常: {e!r}")
    return None


def check_limit_skip(code, side, last_price):
    """判断是否应跳过（触板无法成交）。side: 'BUY'/'SELL'。返回 (skip, note)。"""
    lim = get_limit_prices(code)
    if not lim:
        return False, "无涨跌停数据，不跳过"
    up, down = lim
    if side == "BUY" and last_price >= up:
        return True, f"现价{last_price:.2f}≥涨停{up:.2f}，买单无法成交"
    if side == "SELL" and last_price <= down:
        return True, f"现价{last_price:.2f}≤跌停{down:.2f}，卖单无法成交"
    return False, ""


def _order(trader, account, code, side, vol, price, remark):
    """下单，返回 order_id（>0 成功，-1 失败）。"""
    from xtquant import xtconstant
    if side == "BUY":
        order_type = xtconstant.STOCK_BUY
        price_type = xtconstant.FIX_PRICE if C.BUY_PRICE_TYPE == "FIX" else xtconstant.LATEST_PRICE
        px = price if price_type == xtconstant.FIX_PRICE else 0.0
    else:
        order_type = xtconstant.STOCK_SELL
        price_type = xtconstant.LATEST_PRICE if C.AUTO_SELL_PRICE_TYPE == "LATEST" else xtconstant.FIX_PRICE
        px = 0.0 if price_type == xtconstant.LATEST_PRICE else round(price * 0.995, 2)
    return trader.order_stock(account, code, order_type, vol, price_type, px,
                              "traework_rebalance", remark)


def _fetch_price(code):
    """取最新价。失败返回 None。"""
    try:
        from xtquant import xtdata
        xtdata.subscribe_quote(code, period="tick", count=-1)
        time.sleep(0.3)
        tick = xtdata.get_full_tick([code]).get(code)
        if tick:
            p = float(tick.get("lastPrice", 0))
            return p if p > 0 else None
    except Exception:
        pass
    return None


def _query_order(trader, account, order_id):
    """查指定委托的最新状态。返回 (status, traded_vol, order_vol) 或 None。"""
    try:
        orders = trader.query_stock_orders(account)
        if not orders:
            return None
        for o in orders:
            if int(getattr(o, "order_id", -1)) == order_id:
                return (int(getattr(o, "order_status", 255)),
                        int(getattr(o, "traded_volume", 0)),
                        int(getattr(o, "order_volume", 0)))
    except Exception as e:
        print(f"    !! 查询委托状态异常: {e!r}")
    return None


def wait_order_fill(trader, account, order_id, timeout=WAIT_SECONDS):
    """等待委托成交，单次最多 timeout 秒。返回 (final_status, traded_vol, order_vol)。

    终止条件：全部成交 / 废单 / 已撤 / 部撤 / 超时。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _query_order(trader, account, order_id)
        if r:
            status, traded, vol = r
            if vol > 0 and traded >= vol:
                return ORDER_SUCCEEDED, traded, vol   # 全部成交
            if status in (ORDER_JUNK, ORDER_CANCELED, ORDER_PART_CANCEL):
                return status, traded, vol
            # 已报/部成/待撤 等 → 继续等
        time.sleep(POLL_INTERVAL)
    return 255, 0, 0  # 超时


def order_with_guard(trader, account, code, side, vol, price=None, remark="order_guard"):
    """委托守护：下单→轮询→未全成撤单重试→直到成交或涨跌停跳过。"""
    attempts = 0
    total_traded = 0
    remaining = vol
    last_order_id = -1

    while attempts < MAX_RETRY:
        attempts += 1
        # 刷新价格（重试时价格可能已变化；若传入价无效则现价）
        px = price if price and price > 0 else _fetch_price(code)
        if not px or px <= 0:
            return {"ok": False, "action": "ERROR", "traded_vol": total_traded,
                    "order_id": last_order_id, "attempts": attempts,
                    "note": f"{code} 取价失败，无法委托"}
        # 涨跌停跳过检查
        skip, note = check_limit_skip(code, side, px)
        if skip:
            return {"ok": False, "action": "LIMIT_SKIP", "traded_vol": total_traded,
                    "order_id": last_order_id, "attempts": attempts, "note": note}

        oid = _order(trader, account, code, side, remaining, px, remark)
        last_order_id = oid
        if oid is None or oid <= 0:
            print(f"    !! {code} 委托失败(order_stock 返回 {oid})，{note or ''}")
            continue  # 重试

        print(f"    委托#{attempts} {code} {side} {remaining}股 @{px:.2f} -> order={oid}（等待成交最多{WAIT_SECONDS}s）")
        status, traded, order_vol = wait_order_fill(trader, account, oid)
        total_traded += traded

        if status == ORDER_SUCCEEDED:
            return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                    "order_id": oid, "attempts": attempts,
                    "note": f"{code} 委托#{attempts} 全部成交 {total_traded}股 @{px:.2f}"}
        if status in (ORDER_JUNK,):
            print(f"    !! {code} 委托废单，重试")
            continue
        if status in (ORDER_CANCELED, ORDER_PART_CANCEL):
            print(f"    !  {code} 委托已撤/部撤，重试剩余 {remaining - traded} 股")
            remaining -= traded
            if remaining <= 0:
                return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                        "order_id": oid, "attempts": attempts,
                        "note": f"{code} 已全部成交(含部成) {total_traded}股"}
            continue

        # 超时未全成 → 撤单 → 重试剩余
        cancel = trader.cancel_order_stock(account, oid)
        if cancel == 0:
            print(f"    !  {code} 等待超时未全成(成交{traded}/{order_vol})，已撤单，更新价格重试剩余")
        else:
            print(f"    !! {code} 撤单失败(cancel={cancel})，稍后重试")
        remaining = max(0, remaining - traded)
        if remaining <= 0:
            return {"ok": True, "action": "FILLED", "traded_vol": total_traded,
                    "order_id": oid, "attempts": attempts,
                    "note": f"{code} 已全部成交(含部成) {total_traded}股"}
        time.sleep(2)

    return {"ok": False, "action": "CANCELED_TIMEOUT", "traded_vol": total_traded,
            "order_id": last_order_id, "attempts": attempts,
            "note": f"{code} 重试 {MAX_RETRY} 次仍未全部成交，放弃（已成交 {total_traded}股）"}
