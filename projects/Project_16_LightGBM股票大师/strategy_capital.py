# coding: utf-8
"""策略资金池计算：初始启动资金 + 已实现盈亏 + 策略持仓浮盈 = 当前可加仓额度。

账户约 1000 万，但策略只用 START_CAPITAL 建仓；收益（已实现 + 浮盈）滚动进资金池，
下次买入的 --total 用此资金池，实现"赚的钱滚动加仓"。

输出：
  - 控制台打印资金池构成
  - 写入 data/strategy_capital.json（供 9:45 买入任务读取 --total）

用法：
  python strategy_capital.py
"""
import csv
import json
import os
import sys
import time
from collections import defaultdict, deque

import qmt_config as C

sys.path.append(C.XTPACK)

CAP_FILE = os.path.join(os.path.dirname(C.SIGNAL_FILE), "strategy_capital.json")


def _buy_fee(amt, code):
    """买入手续费：佣金(最低5元) + 沪市过户费。"""
    return max(C.COMM_MIN, amt * C.COMM_RATE) + (amt * C.TRANS_RATE if code.startswith("6") else 0.0)


def _sell_fee(amt, code):
    """卖出手续费：佣金(最低5元) + 印花税 + 沪市过户费。"""
    return (max(C.COMM_MIN, amt * C.COMM_RATE) + amt * C.STAMP_RATE
            + (amt * C.TRANS_RATE if code.startswith("6") else 0.0))


def calc_realized():
    """FIFO 配对成交记录，按实盘费率扣交易成本，计算已实现盈亏（净额）。

    买入按含费成本入账（成交额 + 买入手续费），卖出按净回笼（成交额 - 卖出手续费），
    已实现 = Σ 卖出净回笼 - Σ 配对买入含费成本。结算口径统一用实盘费率（qmt_config）。
    """
    q = defaultdict(deque)  # code -> deque[(vol, cost_per_share含费)]
    realized = 0.0
    if not os.path.exists(C.TRADE_LOG):
        return 0.0
    rows = []
    with open(C.TRADE_LOG, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    rows.sort(key=lambda r: r.get("time", ""))
    for r in rows:
        code, side = r.get("code", ""), r.get("side", "")
        try:
            vol = int(float(r["vol"]))
            price = float(r["price"])
        except (TypeError, ValueError, KeyError):
            continue
        if side == "BUY":
            amt = price * vol
            q[code].append((vol, (amt + _buy_fee(amt, code)) / vol))
        elif side == "SELL":
            if price <= 0:  # 市价清仓记录价格可能为 0，跳过避免污染盈亏
                continue
            sell_vol = vol
            while sell_vol > 0 and q[code]:
                v, cost_per = q[code][0]
                take = min(v, sell_vol)
                sell_amt = price * take
                realized += (sell_amt - _sell_fee(sell_amt, code)) - cost_per * take
                sell_vol -= take
                q[code][0] = (v - take, cost_per)
                if q[code][0][0] <= 0:
                    q[code].popleft()
    return realized


def _query_positions_with_retry(trader, account, retries=2, wait=8):
    """query_stock_positions 非交易时段不稳定，重试直至非空。"""
    for i in range(retries + 1):
        time.sleep(wait)
        positions = trader.query_stock_positions(account)
        if positions:
            return positions
        print(f"    持仓查询第 {i+1} 次为空，重试 ...")
    return None


def calc_position_float():
    """查询 QMT 实际持仓中"策略买入过"的股票浮盈（成本用成交记录 BUY 价）。"""
    float_pnl = 0.0
    # 策略持仓 = 成交记录里 BUY 过的代码（账户历史持仓不计入）
    cost_map = {}
    if os.path.exists(C.TRADE_LOG):
        with open(C.TRADE_LOG, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("side") == "BUY":
                    try:
                        cost_map[row["code"]] = float(row["price"])
                    except (TypeError, ValueError):
                        pass
    if not cost_map:
        return 0.0
    try:
        from xtquant import xttrader, xttype
        trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
        trader.start()
        if trader.connect() != 0:
            return 0.0
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        positions = _query_positions_with_retry(trader, account)
        if not positions:
            print("    !! 多次查询持仓仍为空，浮盈按 0 计（非交易时段常见）")
            trader.stop()
            return 0.0
        # 取现价兜底（market_value 可能缺失）
        prices = {}
        try:
            from xtquant import xtdata
            codes = [getattr(p, "stock_code", "") for p in positions if getattr(p, "stock_code", "") in cost_map]
            if codes:
                xtdata.subscribe_quote(codes, period="tick", count=-1)
                time.sleep(1)
                ticks = xtdata.get_full_tick(codes)
                prices = {c: float(t.get("lastPrice", 0) or 0) for c, t in ticks.items()}
        except Exception:
            pass
        for p in positions:
            code = getattr(p, "stock_code", "")
            if code not in cost_map:
                continue
            vol = int(getattr(p, "volume", 0) or 0)
            if vol <= 0:
                # QMT 当天卖出后，持仓列表仍会显示该股但数量为 0（次日才清除）。
                # 判断持仓必须以持仓数量 >0 为准，vol=0 即已卖出，必须过滤，否则误算浮盈/资金池。
                continue
            cost = getattr(p, "open_price", 0) or cost_map.get(code, 0)
            mkt = getattr(p, "market_value", 0) or 0
            if mkt > 0:
                float_pnl += mkt - cost * vol
            elif prices.get(code, 0) > 0:
                float_pnl += (prices[code] - cost) * vol
        trader.stop()
    except Exception as e:
        print(f"    !! 查询持仓浮盈异常: {e!r}（按 0 计）")
    return float_pnl


def main():
    realized = calc_realized()
    float_pnl = calc_position_float()
    capital = C.START_CAPITAL + realized + float_pnl
    print(f"策略资金池 = 初始 {C.START_CAPITAL:,.0f} + 已实现盈亏 {realized:,.0f} + 策略持仓浮盈 {float_pnl:,.0f}")
    print(f"             = {capital:,.0f} 元（下次买入 --total 用此值，仓位上限 {capital * (1 - C.RESERVE_CASH_PCT):,.0f}）")
    out = {
        "start": C.START_CAPITAL,
        "realized": round(realized, 2),
        "float_pnl": round(float_pnl, 2),
        "capital": round(capital, 2),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    os.makedirs(os.path.dirname(CAP_FILE), exist_ok=True)
    with open(CAP_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("已写入:", CAP_FILE)


if __name__ == "__main__":
    main()
