# coding: utf-8
"""方案A·每日换仓执行层：持仓 vs 当日评分 top2 → 卖被PK的、买新晋的。

逻辑（每日 9:45，开盘15分钟后资金流稳定）：
  1) 读当日完整版清单 data/selections/<date>_selection_full.csv
  2) 目标持仓 = 清单中 total>=红线(58) 的前 TOP(2) 只（按 total 降序）
  3) 读当前策略持仓（QMT 实时，volume>0 才算持仓；成本用 open_price，缺则本地买入价补）
  4) 卖出：持仓中不在目标 top2 的 → 卖（数量=今日可卖 can_use_volume；T+1 锁定的今天不卖）
  5) 买入：目标 top2 中未持有的 → 买（等权，预算=可用资金95%）
  6) 边界：价格风控(止损/止盈/移动止盈)由 qmt_monitor 六档盯盘负责，本脚本只做评分换仓；
     新票必须 total>=红线，不足则宁缺毋滥。

用法：
  python rebalance_daily.py                    # 默认今天，dry-run
  python rebalance_daily.py --date 20260821    # 指定日期
  python rebalance_daily.py --live             # 真实换仓（先卖后买，慎用）
输出：
  data/rebalance_<date>.md / .json（换仓计划或执行结果）
"""
import argparse
import csv
import json
import os
import sys
import time

import qmt_config as C

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)

REDLINE = 58.0
TOP_N = 2


def today_str():
    return time.strftime("%Y%m%d")


def load_selection(csv_path, top_n):
    """读完整版清单，返回 total>=REDLINE 按 total 降序的前 top_n 只。"""
    stocks = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("ts_code") or "").strip()
            if not code:
                continue
            try:
                total = float(row.get("total", 0) or 0)
            except ValueError:
                total = 0.0
            if total >= REDLINE:
                stocks.append({"code": code, "name": row.get("name", ""),
                               "total": total, "prob": float(row.get("model_prob", 0) or 0)})
    stocks.sort(key=lambda s: s["total"], reverse=True)
    return stocks[:top_n]


def load_positions_qmt():
    """QMT 实时持仓（volume>0 才算持仓）。返回 (positions, vols)；vols=今日可卖 can_use_volume。"""
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            return {}, {}
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(5)
        positions = trader.query_stock_positions(account)
        if not positions:
            return {}, {}
        pos, vol = {}, {}
        for p in positions:
            code = getattr(p, "stock_code", "")
            v = int(getattr(p, "can_use_volume", 0) or getattr(p, "volume", 0))
            if not code or v <= 0:
                continue
            pos[code] = float(getattr(p, "open_price", 0) or 0)
            vol[code] = v
        return pos, vol
    except Exception as e:
        print(f"    !! QMT 查询持仓异常: {e!r}")
        return {}, {}
    finally:
        trader.stop()


def load_positions_log():
    """从 qmt_trade_log 估算持仓与可卖（BUY 累计 - SELL 累计；今日买入标记 T+1 锁定）。"""
    if not os.path.exists(C.TRADE_LOG):
        return {}, {}
    bought, sold = {}, {}
    today = time.strftime("%Y-%m-%d")
    for row in csv.DictReader(open(C.TRADE_LOG, encoding="utf-8-sig")):
        code = row.get("code", "")
        if not code:
            continue
        try:
            vol = int(float(row.get("vol", 0) or 0))
        except ValueError:
            vol = 0
        side = row.get("side", "")
        if side == "BUY":
            bought[code] = bought.get(code, 0) + vol
            if (row.get("time") or "").startswith(today):
                bought.setdefault("_locked_today", set()).add(code)
        elif side == "SELL":
            sold[code] = sold.get(code, 0) + vol
    pos, vol = {}, {}
    for c, b in bought.items():
        net = b - sold.get(c, 0)
        if net > 0:
            pos[c] = 0.0  # 成本从 CSV 补
            locked = c in bought.get("_locked_today", set())
            vol[c] = 0 if locked else net  # 今日买入 T+1 锁定不可卖
    return pos, vol


def _connect():
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    if trader.connect() != 0:
        raise RuntimeError("连接 miniQMT 失败")
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    time.sleep(3)
    return trader, account


def _fetch_price(code):
    try:
        from xtquant import xtdata
        xtdata.subscribe_quote(code, period="tick", count=-1)
        time.sleep(0.3)
        tick = xtdata.get_full_tick([code]).get(code)
        if tick:
            return float(tick.get("lastPrice", 0))
    except Exception as e:
        print(f"    !! 取价失败 {code}: {e}")
    return None


def _sell(trader, account, code, vol, price):
    from xtquant import xtconstant
    price_type = xtconstant.LATEST_PRICE if C.AUTO_SELL_PRICE_TYPE == "LATEST" else xtconstant.FIX_PRICE
    px = 0.0 if price_type == xtconstant.LATEST_PRICE else round(price * 0.995, 2)
    return trader.order_stock(account, code, xtconstant.STOCK_SELL, vol, price_type, px,
                              "traework_rebalance", "planA_pk_out")


def _buy(trader, account, code, vol, price):
    from xtquant import xtconstant
    price_type = xtconstant.FIX_PRICE if C.BUY_PRICE_TYPE == "FIX" else xtconstant.LATEST_PRICE
    return trader.order_stock(account, code, xtconstant.STOCK_BUY, vol, price_type, price,
                              "traework_rebalance", "planA_new_top")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today_str(), help="清单日期 YYYYMMDD（默认今天）")
    ap.add_argument("--top", type=int, default=TOP_N, help="目标持仓数（大盘降半仓传 1）")
    ap.add_argument("--live", action="store_true", help="真实换仓（默认 dry-run）")
    args = ap.parse_args()

    sel_path = os.path.join(C.TRADE_LOG.rsplit("qmt_trade_log.csv", 1)[0], "selections", f"{args.date}_selection_full.csv")
    if not os.path.exists(sel_path):
        print(f"!! 找不到当日清单: {sel_path}\n   先运行 review_full 生成完整版清单")
        return
    target = load_selection(sel_path, args.top)
    if not target:
        print(f"!! 当日清单过红线(>{REDLINE:.0f})的票为 0，本日不换仓（宁缺毋滥）")
        return

    # 策略持仓定义：只认成交记录里 BUY 过的代码（账户历史持仓不归本脚本管，由清仓任务负责）
    strategy_codes = set()
    if os.path.exists(C.TRADE_LOG):
        for row in csv.DictReader(open(C.TRADE_LOG, encoding="utf-8-sig")):
            if row.get("side") == "BUY" and row.get("code"):
                strategy_codes.add(row["code"].strip())

    # 持仓（QMT 优先 ∩ 策略持仓；QMT 失败回退日志）
    positions, vols = load_positions_qmt()
    src = "QMT"
    if positions:
        positions = {c: v for c, v in positions.items() if c in strategy_codes}
        vols = {c: vols[c] for c in positions}
        if not positions:
            src = "QMT(无策略持仓)"
    if not positions:
        positions, vols = load_positions_log()
        src = "成交记录(估算)"
    if not positions:
        print("!! 无策略持仓（QMT 与成交记录均无），本日只做买入计划")
        positions, vols = {}, {}

    target_codes = {t["code"] for t in target}
    sell_plan = []  # 被 PK 出局的可卖持仓
    hold_plan = []
    locked = []
    for code, cost in positions.items():
        if code in target_codes:
            hold_plan.append(code)
            continue
        v = vols.get(code, 0)
        if v > 0:
            sell_plan.append({"code": code, "cost": cost, "vol": v})
        else:
            locked.append(code)  # T+1 锁定，今天不能卖
    buy_plan = [t for t in target if t["code"] not in positions]

    print("=" * 64)
    print(f"[方案A 换仓计划] {args.date} | 清单: {os.path.basename(sel_path)}")
    print(f"  持仓来源: {src} | 当前持仓 {len(positions)} 只 | 目标 top{TOP_N}: {[t['code'] for t in target]}")
    print(f"  {'卖出(PK出局)':<16}{'数量':<8}{'动作'}")
    for s in sell_plan:
        print(f"  {s['code']:<16}{s['vol']:<8}卖出")
    for c in locked:
        print(f"  {c:<16}{'':<8}⚠️ T+1 锁定今日不卖")
    for c in hold_plan:
        print(f"  {c:<16}{'':<8}持有")
    print(f"  {'买入(新晋top)':<16}{'评分':<8}{'动作'}")
    for b in buy_plan:
        print(f"  {b['code']:<16}{b['total']:<8.1f}买入")
    print("=" * 64)

    if not args.live:
        print("DRY-RUN：未产生委托。确认后加 --live 执行真实换仓。")
        # 落盘计划
        plan = {"date": args.date, "target": target, "sell": sell_plan,
                "buy": buy_plan, "hold": hold_plan, "locked": locked}
        out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
        print("    计划已保存:", out)
        return

    # ---- LIVE 执行：先卖后买 ----
    from xtquant import xtconstant
    trader, account = _connect()
    asset = trader.query_stock_asset(account)
    cash = float(asset.cash) if asset else 0.0
    print(f"  [LIVE] 可用资金 {cash:,.2f}")
    log_rows = []

    # 1) 卖出被 PK 的持仓
    for s in sell_plan:
        price = _fetch_price(s["code"])
        if not price or price <= 0:
            print(f"    !! {s['code']} 取价失败，跳过卖出")
            continue
        oid = _sell(trader, account, s["code"], s["vol"], price)
        print(f"    卖出 {s['code']} {s['vol']}股 @{price:.2f} -> order={oid}")
        log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), s["code"], "SELL", s["vol"], price, "PK_OUT", oid])

    # 2) 买入新晋 top
    if buy_plan:
        n = len(buy_plan)
        per = cash * (1 - C.RESERVE_CASH_PCT) / n
        for b in buy_plan:
            price = _fetch_price(b["code"])
            if not price or price <= 0:
                print(f"    !! {b['code']} 取价失败，跳过买入")
                continue
            vol = int(per / (price * 100)) * 100
            if vol < C.MIN_ORDER_VOL:
                print(f"    !! {b['code']} 预算不足一手，跳过")
                continue
            oid = _buy(trader, account, b["code"], vol, price)
            print(f"    买入 {b['code']} {vol}股 @{price:.2f} -> order={oid}")
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), b["code"], "BUY", vol, price, b["total"], oid])

    if log_rows and C.TRADE_LOG:
        os.makedirs(os.path.dirname(C.TRADE_LOG), exist_ok=True)
        with open(C.TRADE_LOG, "a", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            if os.path.getsize(C.TRADE_LOG) == 0:
                w.writerow(["time", "code", "side", "vol", "price", "score", "order_id"])
            w.writerows(log_rows)
        print("    成交记录 ->", C.TRADE_LOG)
    trader.stop()


if __name__ == "__main__":
    main()
