# coding: utf-8
"""方案A·每日换仓执行层：持仓 vs 当日评分 top2 → 卖被PK的、买新晋的（等权对齐，策略资金池为基准）。

逻辑（每日 9:45，开盘15分钟后资金流稳定）：
  1) 读当日完整版清单 data/selections/<date>_selection_full.csv
  2) 目标持仓 = 清单中 total>=红线(58) 的前 TOP(2) 只（按 total 降序）
  3) 读当前策略持仓（QMT 实时，volume>0 才算持仓；成本用 open_price，缺则本地买入价补）
  4) 卖出：持仓中不在目标 top2 的 → 卖（数量=今日可卖 can_use_volume；T+1 锁定的今天不卖）
  5) 买入（等权对齐，2026-08-24 修复）：
     - 资金池基准 = strategy_capital.json 的 capital（初始10万 + 已实现盈亏 + 策略持仓浮盈），
       收益滚动、亏损不补；买入预算绝不用账户全量资金。
     - 每只目标市值 = 资金池 × 95% ÷ 目标数。
     - 对每只 target：按"目标股数 = 目标市值÷现价(整手向下取整)"对齐，当前持有股数：
       不足 → 补仓到目标股数；超配 → 减仓到目标股数（受今日可卖限制）。
     ⚠️ 修复前 bug1：买入用了账户全量可用资金(asset.cash)，导致新晋票独吞 95% 资金
        （如 300684 单票买进 939 万，占账户 96%，远超 10 万策略资金池）。
        bug2：只给"未持有的新晋票"分配资金，已持有的 target 不补仓。
        bug3(本次修复)：超配减仓按"市值差额取整"算股数，导致减仓后剩余市值仍远超目标
        （300684 减到 5,200 股≈44万，仍超配9倍）；改为按目标股数对齐，减到目标市值。
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
import strategy_capital as SC

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)

REDLINE = 58.0
TOP_N = 2
CAP_FILE = os.path.join(os.path.dirname(C.TRADE_LOG), "strategy_capital.json")


def load_strategy_capital():
    """读取策略资金池 = 初始10万 + 已实现盈亏 + 策略持仓浮盈（strategy_capital.json）。

    策略只用 START_CAPITAL 建仓，收益滚动进资金池，亏损不补资金；
    买入预算一律以资金池为准，绝不用账户全量资金。
    文件缺失时回退 START_CAPITAL。
    """
    try:
        if os.path.exists(CAP_FILE):
            data = json.load(open(CAP_FILE, encoding="utf-8"))
            cap = float(data.get("capital", 0) or 0)
            if cap > 0:
                return cap
    except Exception as e:
        print(f"    !! 读取资金池失败: {e!r}，回退 START_CAPITAL")
    return float(C.START_CAPITAL)


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
    """QMT 实时持仓。返回 (positions, volumes, sellable)。

    positions: {code: open_price}  所有 volume>0 的持仓（含 T+1 锁定，用于算持有市值/判断是否持有）
    volumes:   {code: volume}       总持有量
    sellable:  {code: can_use_volume} 今日可卖量（T+1 锁定的为 0）
    """
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(C.USERDATA, int(time.time()))
    trader.start()
    try:
        if trader.connect() != 0:
            return {}, {}, {}
        account = xttype.StockAccount(C.ACCOUNT_ID)
        trader.subscribe(account)
        time.sleep(5)
        positions = trader.query_stock_positions(account)
        if not positions:
            return {}, {}, {}
        pos, volumes, sellable = {}, {}, {}
        for p in positions:
            code = getattr(p, "stock_code", "")
            total = int(getattr(p, "volume", 0) or 0)
            if not code or total <= 0:
                continue
            pos[code] = float(getattr(p, "open_price", 0) or 0)
            volumes[code] = total
            sellable[code] = int(getattr(p, "can_use_volume", 0) or 0)
        return pos, volumes, sellable
    except Exception as e:
        print(f"    !! QMT 查询持仓异常: {e!r}")
        return {}, {}, {}
    finally:
        trader.stop()


def load_positions_log():
    """从 qmt_trade_log 估算持仓。返回 (positions, volumes, sellable)。

    positions: {code: cost(0占位)}  BUY-SELL 净持仓>0
    volumes:   {code: net}          总持有量
    sellable:  {code: net}          可卖量（今日买入的 T+1 锁定为 0）
    """
    if not os.path.exists(C.TRADE_LOG):
        return {}, {}, {}
    bought, sold = {}, {}
    today = time.strftime("%Y-%m-%d")
    locked_codes = set()
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
                locked_codes.add(code)
        elif side == "SELL":
            sold[code] = sold.get(code, 0) + vol
    pos, volumes, sellable = {}, {}, {}
    for c, b in bought.items():
        net = b - sold.get(c, 0)
        if net > 0:
            pos[c] = 0.0
            volumes[c] = net
            sellable[c] = 0 if c in locked_codes else net
    return pos, volumes, sellable


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
    positions, volumes, sellable = load_positions_qmt()
    src = "QMT"
    if positions:
        positions = {c: v for c, v in positions.items() if c in strategy_codes}
        volumes = {c: volumes[c] for c in positions}
        sellable = {c: sellable[c] for c in positions}
        if not positions:
            src = "QMT(无策略持仓)"
    if not positions:
        positions, volumes, sellable = load_positions_log()
        src = "成交记录(估算)"
    if not positions:
        print("!! 无策略持仓（QMT 与成交记录均无），本日只做买入计划")
        positions, volumes, sellable = {}, {}, {}

    target_codes = {t["code"] for t in target}
    sell_plan = []  # 被 PK 出局的可卖持仓
    hold_plan = []
    locked = []
    for code, cost in positions.items():
        if code in target_codes:
            hold_plan.append(code)
            continue
        v = sellable.get(code, 0)
        if v > 0:
            sell_plan.append({"code": code, "cost": cost, "vol": v})
        else:
            locked.append(code)  # T+1 锁定，今天不能卖
    # 等权目标数 = 实际目标数（含已持有的 target）
    n_target = max(len(target), 1)

    # 策略资金池（初始10万 + 已实现盈亏 + 策略持仓浮盈）；买入预算一律以此为准，绝不用账户全量资金
    capital = load_strategy_capital()
    target_value = capital * (1 - C.RESERVE_CASH_PCT) / n_target  # 每只 target 目标市值

    # 对每只 target 计算目标股数与当前持有股数的差（按目标股数，而非市值差额）：
    #   目标股数 = 目标市值 ÷ 现价（向下取整到整手），避免市值差额取整后仍超配；
    #   不足 → 补仓到目标股数；超配 → 减仓到目标股数（受今日可卖限制）。
    buy_orders = []   # 需买入
    trim_orders = []  # 需减仓（超配回目标）
    for t in target:
        code = t["code"]
        price = _fetch_price(code)
        if not price or price <= 0:
            print(f"    !! {code} 取价失败，跳过该票调整")
            continue
        held_vol = volumes.get(code, 0)
        held_value = held_vol * price
        target_vol = int(target_value / price / 100) * 100  # 目标股数（整手向下取整，不超目标市值）
        diff = held_vol - target_vol
        if abs(diff) < C.MIN_ORDER_VOL:
            buy_orders.append({"code": code, "vol": 0, "held_vol": held_vol, "held_value": held_value,
                               "price": price, "total": t["total"], "action": "达标不动"})
            continue
        if diff < 0:
            # 不足 → 补仓到目标股数
            vol = -diff
            if vol >= C.MIN_ORDER_VOL:
                buy_orders.append({"code": code, "vol": vol, "held_vol": held_vol, "held_value": held_value,
                                   "price": price, "total": t["total"], "action": "补仓"})
            else:
                buy_orders.append({"code": code, "vol": 0, "held_vol": held_vol, "held_value": held_value,
                                   "price": price, "total": t["total"], "action": "补仓不足一手"})
        else:
            # 超配 → 减仓到目标股数（受今日可卖限制）
            trim_vol = min(diff, sellable.get(code, 0))
            if trim_vol >= C.MIN_ORDER_VOL:
                trim_orders.append({"code": code, "vol": trim_vol, "held_vol": held_vol, "held_value": held_value,
                                    "price": price, "total": t["total"], "action": "减仓超配"})
            else:
                buy_orders.append({"code": code, "vol": 0, "held_vol": held_vol, "held_value": held_value,
                                   "price": price, "total": t["total"], "action": "超配但T+1锁定/不足一手"})

    print("=" * 64)
    print(f"[方案A 换仓计划·等权对齐] {args.date} | 清单: {os.path.basename(sel_path)}")
    print(f"  持仓来源: {src} | 当前持仓 {len(positions)} 只 | 目标 top{n_target}: {[t['code'] for t in target]}")
    print(f"  策略资金池 ≈ {capital:,.0f} 元（初始10万+盈亏滚动，不使用账户全量资金）")
    print(f"  每只目标市值 ≈ {target_value:,.0f} 元 = 资金池×{1 - C.RESERVE_CASH_PCT:.0%}÷{n_target}")
    print(f"  {'卖出(PK出局)':<14}{'数量':<8}{'动作'}")
    for s in sell_plan:
        print(f"  {s['code']:<14}{s['vol']:<8}卖出")
    for c in locked:
        print(f"  {c:<14}{'':<8}⚠️ T+1 锁定今日不卖")
    print(f"  {'目标调整':<14}{'当前→目标':<22}{'动作'}")
    for o in buy_orders:
        print(f"  {o['code']:<14}{o['held_value']:>12,.0f}→{target_value:>10,.0f}  {o['action']}")
    for o in trim_orders:
        print(f"  {o['code']:<14}{o['held_value']:>12,.0f}→{target_value:>10,.0f}  {o['action']}")
    print("=" * 64)

    if not args.live:
        print("DRY-RUN：未产生委托。确认后加 --live 执行真实换仓。")
        # 落盘计划
        plan = {"date": args.date, "target": target,
                "strategy_capital": round(capital, 2), "target_value_each": round(target_value, 2),
                "sell": sell_plan, "buy": [o for o in buy_orders if o.get("vol")],
                "trim": trim_orders, "hold": hold_plan, "locked": locked}
        out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
        print("    计划已保存:", out)
        return

    # ---- LIVE 执行：先卖后买（等权对齐，资金池为基准，委托守护重试）----
    import order_guard
    from xtquant import xtconstant
    trader, account = _connect()
    asset = trader.query_stock_asset(account)
    cash = float(asset.cash) if asset else 0.0
    print(f"  [LIVE] 账户可用资金 {cash:,.2f} | 策略资金池 {capital:,.0f} 元 | 每只目标市值 ≈ {target_value:,.0f} 元")
    log_rows = []
    guard_note = []  # 委托守护结果汇总

    # 1) 卖出被 PK 的持仓（委托守护：未成撤单重试，涨跌停跳过）
    for s in sell_plan:
        price = _fetch_price(s["code"])
        if not price or price <= 0:
            print(f"    !! {s['code']} 取价失败，跳过卖出")
            continue
        r = order_guard.order_with_guard(trader, account, s["code"], "SELL", s["vol"], price, "planA_pk_out")
        print(f"    卖出 {s['code']} {s['vol']}股 -> {r['note']}")
        guard_note.append(f"卖{s['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), s["code"], "SELL",
                             r["traded_vol"], price, "PK_OUT", r["order_id"]])

    # 2) 减仓超配 target（先卖，回笼资金；委托守护）
    for t in trim_orders:
        r = order_guard.order_with_guard(trader, account, t["code"], "SELL", t["vol"], t["price"], "planA_trim")
        print(f"    减仓(超配) {t['code']} {t['vol']}股 -> {r['note']}")
        guard_note.append(f"减{t['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), t["code"], "SELL",
                             r["traded_vol"], t["price"], "TRIM_OVR", r["order_id"]])

    # 3) 补仓/买入 target（委托守护；每笔前用实际可用资金做上限，防超买）
    for b in buy_orders:
        if not b.get("vol"):
            continue
        asset2 = trader.query_stock_asset(account)
        cash_now = float(asset2.cash) if asset2 else cash
        max_vol = int(cash_now / (b["price"] * 100)) * 100
        vol = min(b["vol"], max_vol)
        if vol < C.MIN_ORDER_VOL:
            print(f"    !! {b['code']} 可用资金不足一手({C.MIN_ORDER_VOL}股)，跳过")
            continue
        r = order_guard.order_with_guard(trader, account, b["code"], "BUY", vol, b["price"], "planA_new_top")
        print(f"    买入 {b['code']} {vol}股 -> {r['note']}")
        guard_note.append(f"买{b['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), b["code"], "BUY",
                             r["traded_vol"], b["price"], b["total"], r["order_id"]])

    if guard_note:
        print("  [委托守护] " + " | ".join(guard_note))

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
