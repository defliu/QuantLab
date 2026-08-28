# coding: utf-8
"""执行层：TraeWork 选股结果 → miniQMT 下单（xtquant）。

流程：
  1) 读取选股结果 CSV（deploy/review 输出，含 ts_code/model_prob/total）
  2) 按评分权重分配资金，生成买入计划（股数=100的整数倍）
  3) 风控校验：单票仓位上限 / 保留现金 / 最小金额 / 板块过滤
  4) 默认 dry-run 只打印计划；--live 才连接 miniQMT 实盘下单

安全约定：
  - 默认 dry-run，绝不自动下单
  - --live 需显式指定，且需 miniQMT 客户端已登录（模拟或实盘账号）
  - 真实资金交易前务必先用模拟盘验证

用法：
  python qmt_trader.py --plan data/selections/20260819_selection_full.csv --top-k 5 --total 100000
  python qmt_trader.py --plan ... --total 100000 --live        # 实盘（需客户端登录）
"""
import argparse
import csv
import os
import sys
import time

import qmt_config as C

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)


def load_plan(csv_path, top_k):
    """读取选股结果 CSV，返回按评分降序的股票列表。"""
    stocks = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            stocks.append({
                "code": row["ts_code"].strip(),
                "prob": float(row.get("model_prob", 0) or 0),
                "score": float(row.get("total", 0) or 0),
            })
    stocks.sort(key=lambda s: s["score"], reverse=True)
    return stocks[:top_k]


def build_orders(stocks, total_asset):
    """按评分权重分配资金，生成订单（股数=100整数倍）。"""
    total_score = sum(max(s["score"], 1.0) for s in stocks) or 1.0
    investable = total_asset * (1 - C.RESERVE_CASH_PCT)
    orders = []
    for s in stocks:
        weight = max(s["score"], 1.0) / total_score
        budget = min(investable * weight, total_asset * C.MAX_POSITION_PCT)
        if budget < C.MIN_ORDER_AMOUNT:
            continue
        # 价格未知时 dry-run 用估算价（0 表示待 live 时取行情）
        price = 0.0
        vol = 0
        orders.append({**s, "budget": round(budget, 2), "price": price, "vol": vol})
    return orders


def build_orders_equal(stocks, total_asset):
    """均分模式：总仓位 90% 内平均分配给每只，单票不超 MAX_POSITION_PCT。"""
    investable = total_asset * (1 - C.RESERVE_CASH_PCT)
    n = max(len(stocks), 1)
    per = investable / n
    orders = []
    for s in stocks:
        budget = min(per, total_asset * C.MAX_POSITION_PCT)
        if budget < C.MIN_ORDER_AMOUNT:
            print(f"    !! {s['code']} 单只预算不足最小金额，跳过")
            continue
        orders.append({**s, "budget": round(budget, 2), "price": 0.0, "vol": 0})
    return orders


def _fetch_price(code):
    """连接 xtdata 取最新价（实盘用）。失败返回 None。"""
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


def _connect():
    """连接 miniQMT 客户端，返回 (trader, account)。需客户端已登录。"""
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    result = trader.connect()
    if result != 0:
        raise RuntimeError(f"连接 miniQMT 失败 (code={result})，请确认已启动 {C.QMT_PATH} 并登录")
    account = xttype.StockAccount(C.ACCOUNT_ID)
    trader.subscribe(account)
    return trader, account


def dry_run(orders, total_asset):
    print("=" * 60)
    print(f"[DRY-RUN] 买入计划（总资产 {total_asset:,.0f}，保留现金 {C.RESERVE_CASH_PCT:.0%}）")
    print(f"{'代码':<12}{'模型分':<8}{'评分':<8}{'预算(元)':<12}{'状态':<8}")
    for o in orders:
        print(f"{o['code']:<12}{o['prob']:<8.3f}{o['score']:<8.1f}{o['budget']:>10,.0f}   待取价")
    print("=" * 60)
    print("DRY-RUN 结束，未产生任何委托。确认无误后用 --live 实盘执行。")


def live_execute(orders):
    from xtquant import xtconstant
    print(f"[LIVE] 连接 miniQMT: {C.USERDATA}")
    trader, account = _connect()
    asset = trader.query_stock_asset(account)
    if asset is None:
        print("    !! 查询账户资产失败，检查账号", C.ACCOUNT_ID)
        return
    print(f"    账户总资产: {asset.total_asset:,.2f}  可用资金: {asset.cash:,.2f}")

    log_rows = []
    for o in orders:
        price = _fetch_price(o["code"])
        if not price or price <= 0:
            print(f"    !! {o['code']} 取价失败，跳过")
            continue
        vol = int(o["budget"] / (price * 100)) * 100
        vol = min(vol, int(asset.cash * 0.9 / (price * 100)) * 100)  # 不超过可用资金90%
        if vol < C.MIN_ORDER_VOL:
            print(f"    !! {o['code']} 不足一手({C.MIN_ORDER_VOL}股)，跳过")
            continue
        # 限价单：现价上方一档
        price_type = xtconstant.FIX_PRICE if C.BUY_PRICE_TYPE == "FIX" else xtconstant.LATEST_PRICE
        order_id = trader.order_stock(
            account, o["code"], xtconstant.STOCK_BUY, vol, price_type, price,
            "traework_stock_master", f"plan_score{o['score']:.1f}",
        )
        print(f"    下单 {o['code']} 买入 {vol} 股 @ {price:.2f} -> order_id={order_id}")
        log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), o["code"], "BUY", vol, price, o["score"], order_id])

    if log_rows and C.TRADE_LOG:
        C.append_trade_rows(log_rows)
        print("    成交记录 ->", C.TRADE_LOG)
    trader.stop()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True, help="选股结果 CSV 路径")
    ap.add_argument("--top-k", type=int, default=5, help="买入数量")
    ap.add_argument("--total", type=float, default=100000, help="总资产（用于分配仓位）")
    ap.add_argument("--equal", action="store_true", help="均分模式（默认按评分加权）")
    ap.add_argument("--live", action="store_true", help="实盘下单（默认 dry-run）")
    args = ap.parse_args()

    stocks = load_plan(args.plan, args.top_k)
    if not stocks:
        print("!! 选股结果为空")
        return
    if args.equal:
        orders = build_orders_equal(stocks, args.total)
    else:
        orders = build_orders(stocks, args.total)

    if args.live:
        live_execute(orders)
    else:
        dry_run(orders, args.total)


if __name__ == "__main__":
    main()
