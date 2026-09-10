# coding: utf-8
"""方案A·每日换仓执行层：持仓 vs 当日评分 top2 → 卖被PK的、买新晋的（等权对齐，策略资金池为基准）。

逻辑（每日 9:45，开盘15分钟后资金流稳定）：
  1) 读当日完整版清单 data/selections/<date>_selection_full.csv
  2) 目标持仓 = 清单中 total>=红线(58) 的前 TOP(2) 只（按 total 降序）
  3) 读当前策略持仓（QMT 实时，volume>0 才算持仓；成本用 open_price，缺则本地买入价补）
  4) 卖出（到期制，替代原 PK_OUT 日频翻转）：持仓掉出目标 top2 且已满 hold_days(默认5) 交易日 → 卖（数量=今日可卖 can_use_volume；T+1 锁定的今天不卖；持仓未满期限者保留，不再日频翻转）
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
from datetime import datetime, date, timedelta

import qmt_config as C
import strategy_capital as SC
import position_filter as PF

# 加载 xtquant（放末尾，避免覆盖本环境的 numpy）
sys.path.append(C.XTPACK)

REDLINE = 58.0
TOP_N = 2
HOLD_DAYS = 5  # 持有期满交易日数（到期制卖出，替代原 PK_OUT 日频翻转；T-20260904-001）
CAP_FILE = os.path.join(os.path.dirname(C.TRADE_LOG), "strategy_capital.json")


def load_strategy_capital():
    """读取策略资金池 = 初始10万 + 已实现盈亏 + 策略持仓浮盈（strategy_capital.json）。

    策略只用 START_CAPITAL 建仓，收益滚动进资金池，亏损不补资金；
    买入预算一律以资金池为准，绝不用账户全量资金。
    文件缺失/账号戳校验失败时回退 START_CAPITAL（校验逻辑见 qmt_config.load_capital_pool）。
    """
    data = C.load_capital_pool()
    if data:
        try:
            cap = float(data.get("capital", 0) or 0)
            if cap > 0:
                return cap
        except (TypeError, ValueError) as e:
            print(f"    !! 资金池字段异常: {e!r}，回退 START_CAPITAL")
    return float(C.START_CAPITAL)


def today_str():
    return time.strftime("%Y%m%d")


def _parse_date(s):
    """'YYYYMMDD' -> date 对象（解析失败回退今天）。"""
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except (ValueError, TypeError):
        return date.today()


def _build_last_buy_dates():
    """从成交记录构建 {code: 最近一次 BUY 日期(date)}，用于到期制持仓天数判定。

    无买入记录（如转入持仓）的 code 不出现在此 dict → 调用方视为已满期（held=999）。
    """
    last = {}
    for row in C.load_trade_log_rows():
        if row.get("side") != "BUY":
            continue
        code = (row.get("code") or "").strip()
        t = row.get("time") or ""
        if not code or not t:
            continue
        fmt = "%Y-%m-%d %H:%M:%S" if " " in t else "%Y-%m-%d"
        try:
            d = datetime.strptime(t[:19] if " " in t else t, fmt).date()
        except ValueError:
            continue
        if code not in last or d > last[code]:
            last[code] = d
    return last


def _trading_days_between(d0, d1):
    """d0 之后到 d1（含）的交易日数（近似：跳过周末，忽略法定假日）。

    买入日 d0 当日不可卖（T+1），从 d0+1 起算持有交易日；用于到期制判定
    （持仓满 HOLD_DAYS 交易日即触发到期卖出）。
    """
    if not d0 or not d1 or d1 <= d0:
        return 0
    n, cur = 0, d0
    one = timedelta(days=1)
    while cur < d1:
        cur += one
        if cur.weekday() < 5:
            n += 1
    return n


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
    rows = C.load_trade_log_rows()
    if not rows:
        return {}, {}, {}
    bought, sold = {}, {}
    today = time.strftime("%Y-%m-%d")
    locked_codes = set()
    for row in rows:
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


def _sell(trader, account, code, vol, price, remark="planA_mature"):
    from xtquant import xtconstant
    price_type = xtconstant.LATEST_PRICE if C.AUTO_SELL_PRICE_TYPE == "LATEST" else xtconstant.FIX_PRICE
    px = 0.0 if price_type == xtconstant.LATEST_PRICE else round(price * 0.995, 2)
    return trader.order_stock(account, code, xtconstant.STOCK_SELL, vol, price_type, px,
                              "traework_rebalance", remark)


def _buy(trader, account, code, vol, price):
    from xtquant import xtconstant
    price_type = xtconstant.FIX_PRICE if C.BUY_PRICE_TYPE == "FIX" else xtconstant.LATEST_PRICE
    return trader.order_stock(account, code, xtconstant.STOCK_BUY, vol, price_type, price,
                              "traework_rebalance", "planA_new_top")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today_str(), help="清单日期 YYYYMMDD（默认今天）")
    ap.add_argument("--top", type=int, default=TOP_N, help="目标持仓数（大盘降半仓传 1）")
    ap.add_argument("--hold-days", type=int, default=HOLD_DAYS, help="持有期满交易日数（到期制卖出，替代 PK_OUT 日频翻转）")
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
    for row in C.load_trade_log_rows():
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
    sell_plan = []  # 到期/调出卖出（含 reason 字段）
    hold_plan = []
    locked = []
    today_d = _parse_date(args.date)
    last_buy = _build_last_buy_dates()
    for code, cost in positions.items():
        if code in target_codes:
            hold_plan.append(code)          # 仍在 top2 → 继续持有（到期也续期，避免同日卖买抖动）
            continue
        # 到期制：掉出 top2 但持仓未满 hold_days 交易日 → 保留，不再日频翻转（修复 PK_OUT train-serving skew, T-20260904-001）
        ent = last_buy.get(code)
        held = _trading_days_between(ent, today_d) if ent else 999
        if held < args.hold_days:
            hold_plan.append(code)
            continue
        v = sellable.get(code, 0)
        if v > 0:
            sell_plan.append({"code": code, "cost": cost, "vol": v, "reason": "MATURE"})
        else:
            locked.append(code)  # T+1 锁定，今天不能卖
    # 等权目标数 = 实际目标数（含已持有的 target）
    n_target = max(len(target), 1)

    # 大盘风控部署比例（WORKFLOW_DEPLOY.md 九 / 2026-08-31 实盘确认）：
    #   T=2 常态满仓：资金池×95%；T=1 降半仓：资金池×50%（只持 1 只，金额半仓）；
    #   T=0 空仓由调用方以 --top 0 处理（本脚本 top=0 时 target 为空即不买）。
    deploy_pct = 0.50 if args.top == 1 else (1 - C.RESERVE_CASH_PCT)

    # 策略资金池（初始10万 + 已实现盈亏 + 策略持仓浮盈）；买入预算一律以此为准，绝不用账户全量资金
    capital = load_strategy_capital()
    target_value = capital * deploy_pct / n_target  # 每只 target 目标市值

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

    # ---- T-20260907-003 修复：持仓数上限（对齐回测 simulate「持仓始终 ≤ TOP」）----
    # 背景：09-07 到期制（T-20260904-001）首个实盘日，非 target 持仓（300413/003005）未满 N=5 被保留，
    #       买入侧无持仓数上限 + P0 校验不含存量市值 → 又买 601999/601579 → 4 只、总占用≈资金池 1.84 倍。
    # 语义：买入名额 = TOP − 卖出后仍持有的票数；补仓（已持有票，不增加持仓数）不受名额限制；
    #       新增买入（held_vol=0，会叠加持仓数）按 total 降序取前 slots 只。
    sell_codes = {s["code"] for s in sell_plan}
    n_after_sell = len([c for c in positions if c not in sell_codes])
    slots = max(0, args.top - n_after_sell)
    _new_buys = [b for b in buy_orders if b.get("vol") and (b.get("held_vol") or 0) <= 0]
    _topups = [b for b in buy_orders if b.get("vol") and (b.get("held_vol") or 0) > 0]
    if len(_new_buys) > slots:
        print(f"    [T-20260907-003] 卖出后仍持 {n_after_sell} 只 ≥ 目标 {args.top}，"
              f"新增买入名额仅 {slots} 只 → 仅保留最高分 {slots} 只（防超买叠加，原计划 {len(_new_buys)} 只新增）")
    _new_buys.sort(key=lambda b: -(b.get("total") or 0))
    exec_buys = _topups + _new_buys[:slots]

    print("=" * 64)
    print(f"[方案A 换仓计划·等权对齐] {args.date} | 清单: {os.path.basename(sel_path)}")
    print(f"  持仓来源: {src} | 当前持仓 {len(positions)} 只 | 目标 top{n_target}: {[t['code'] for t in target]}")
    print(f"  策略资金池 ≈ {capital:,.0f} 元（初始10万+盈亏滚动，不使用账户全量资金）")
    print(f"  每只目标市值 ≈ {target_value:,.0f} 元 = 资金池×{deploy_pct:.0%}÷{n_target}")
    print(f"  {'卖出(到期/调出)':<16}{'数量':<8}{'动作'}")
    for s in sell_plan:
        print(f"  {s['code']:<14}{s['vol']:<8}卖出")
    for c in locked:
        print(f"  {c:<14}{'':<8}⚠️ T+1 锁定今日不卖")
    print(f"  {'目标调整':<14}{'当前→目标':<22}{'动作'}")
    for o in exec_buys:
        print(f"  {o['code']:<14}{o['held_value']:>12,.0f}→{target_value:>10,.0f}  {o['action']}")
    for o in trim_orders:
        print(f"  {o['code']:<14}{o['held_value']:>12,.0f}→{target_value:>10,.0f}  {o['action']}")
    print("=" * 64)

    if not args.live:
        print("DRY-RUN：未产生委托。确认后加 --live 执行真实换仓。")
        # 落盘计划
        plan = {"date": args.date, "target": target,
                "strategy_capital": round(capital, 2), "target_value_each": round(target_value, 2),
                "sell": sell_plan, "buy": [o for o in exec_buys if o.get("vol")],
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

    # 1) 卖出到期/调出持仓（委托守护：未成撤单重试，涨跌停跳过）
    for s in sell_plan:
        price = _fetch_price(s["code"])
        if not price or price <= 0:
            print(f"    !! {s['code']} 取价失败，跳过卖出")
            continue
        reason = s.get("reason", "MATURE")
        remark = "planA_mature" if reason == "MATURE" else "planA_pk_out"
        r = order_guard.order_with_guard(trader, account, s["code"], "SELL", s["vol"], price, remark)
        print(f"    卖出 {s['code']} {s['vol']}股 [{reason}] -> {r['note']}")
        guard_note.append(f"卖{s['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), s["code"], "SELL",
                             r["traded_vol"], price, reason, r["order_id"]])

    # 2) 减仓超配 target（先卖，回笼资金；委托守护）
    for t in trim_orders:
        r = order_guard.order_with_guard(trader, account, t["code"], "SELL", t["vol"], t["price"], "planA_trim")
        print(f"    减仓(超配) {t['code']} {t['vol']}股 -> {r['note']}")
        guard_note.append(f"减{t['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), t["code"], "SELL",
                             r["traded_vol"], t["price"], "TRIM_OVR", r["order_id"]])

    # ---- P0 资金池硬校验（2026-08-27 补强）：买入预算一律以策略资金池为硬上限 ----
    # 教训：8/24 bug1 用账户全量可用资金(asset.cash)买入 300684 939万，远超 10 万资金池。
    # 此处从策略资金池推导买入总额上限与单票上限，下单前逐笔校验，超限直接拒绝+报警（fail-loud）。
    POOL_BUY_CAP = capital * deploy_pct                     # 当日买入总额硬上限 = 资金池×部署比例（T=1 半仓 50%）
    SINGLE_CAP = target_value                               # 单票硬上限 = 资金池×部署比例÷目标数
    if not (POOL_BUY_CAP > 0 and SINGLE_CAP > 0):
        print("    !! [P0-资金池校验] 资金池<=0，拒绝全部买入（防超买）")
        POOL_BUY_CAP = SINGLE_CAP = 0.0
    placed_buy_total = 0.0  # 已放置买入金额累计
    print(f"    [P0-资金池校验] 当日买入上限 {POOL_BUY_CAP:,.0f} 元 | 单票上限 {SINGLE_CAP:,.0f} 元")

    # T-20260907-003 资金存量校验：保留持仓市值 = 卖出计划外持仓 vol × 现价（现价失败用成本兜底）。
    # 与持仓数上限互为第二道保险：即使名额逻辑漏放行，总占用（保留市值+本次买入）也不得突破资金池×deploy_pct。
    retain_value = 0.0
    for _c in positions:
        if _c in sell_codes:
            continue
        _px = _fetch_price(_c)
        if not _px or _px <= 0:
            _px = positions.get(_c) or 0.0
        retain_value += (volumes.get(_c, 0) or 0) * _px
    if retain_value > 0:
        print(f"    [T-20260907-003] 卖出后保留持仓市值 ≈ {retain_value:,.0f} 元（纳入资金池校验）")

    # 3) 补仓/买入 target（委托守护；资金池硬校验 + 持仓数上限 + 实际可用资金，防超买）
    for b in exec_buys:
        if not b.get("vol"):
            continue
        asset2 = trader.query_stock_asset(account)
        cash_now = float(asset2.cash) if asset2 else cash
        max_vol = int(cash_now / (b["price"] * 100)) * 100
        vol = min(b["vol"], max_vol)
        if vol < C.MIN_ORDER_VOL:
            print(f"    !! {b['code']} 可用资金不足一手({C.MIN_ORDER_VOL}股)，跳过")
            continue
        # ---- 低开校验（V1.4，T4）：极端低开(<=-5%)跳过；低开(-5~-3%)需放量承接才放行 ----
        # 与 G2（T3）同规则：003005 型（低开-10%跌停接刀）→ 跳过；601999 型（低开-4.9%放量翻红）→ 放行
        _ohlc = PF.fetch_ohlc(b["code"])
        if _ohlc:
            _avg = PF.recent_avg_vol(b["code"])
            _vr = (_ohlc["vol_lot"] / _avg) if (_avg and _avg > 0) else None
            _skip, _why = PF.gap_guard(b["code"], b["price"], _ohlc["pre_close"], _ohlc["open"], _vr)
            if _skip:
                print(f"    !! {b['code']} 低开拦截: {_why}")
                guard_note.append(f"买{b['code']}:GAP_BLOCK")
                continue
        order_amt = vol * b["price"]
        # P0 硬校验 1：单笔买入额不得超过单票上限（防单票独吞资金池）
        if order_amt > SINGLE_CAP * 1.02:
            print(f"    !! [P0-资金池校验] {b['code']} 单笔 {order_amt:,.0f} > 单票上限 {SINGLE_CAP:,.0f}，拒绝（防超买）")
            guard_note.append(f"买{b['code']}:POOL_BLOCK")
            continue
        # P0 硬校验 2：当日累计买入额不得超过资金池上限
        if placed_buy_total + order_amt > POOL_BUY_CAP * 1.02:
            print(f"    !! [P0-资金池校验] 累计买入 {placed_buy_total + order_amt:,.0f} > 资金池上限 {POOL_BUY_CAP:,.0f}，拒绝（防超买）")
            guard_note.append(f"买{b['code']}:POOL_BLOCK")
            continue
        # T-20260907-003 校验 3：总占用（保留持仓市值 + 已买 + 本单）不得超过资金池×deploy_pct
        # 修复 09-07 超买：卖出未发生时（到期制保留持仓），原校验只算"当日新增"导致突破本金
        if retain_value + placed_buy_total + order_amt > POOL_BUY_CAP * 1.02:
            print(f"    !! [T-20260907-003-存量校验] 保留持仓 {retain_value:,.0f} + 已买 {placed_buy_total:,.0f} + 本单 {order_amt:,.0f} "
                  f"> 资金池上限 {POOL_BUY_CAP:,.0f}，拒绝（防突破本金）")
            guard_note.append(f"买{b['code']}:POOL_BLOCK")
            continue
        placed_buy_total += order_amt
        r = order_guard.order_with_guard(trader, account, b["code"], "BUY", vol, b["price"], "planA_new_top")
        print(f"    买入 {b['code']} {vol}股 -> {r['note']}")
        guard_note.append(f"买{b['code']}:{r['action']}")
        if r["ok"] and r["traded_vol"] > 0:
            log_rows.append([time.strftime("%Y-%m-%d %H:%M:%S"), b["code"], "BUY",
                             r["traded_vol"], b["price"], b["total"], r["order_id"]])

    if guard_note:
        print("  [委托守护] " + " | ".join(guard_note))

    if log_rows and C.TRADE_LOG:
        C.append_trade_rows(log_rows)
        print("    成交记录 ->", C.TRADE_LOG)

    # 保存执行结果（9:45 任务 step4 要求 + 10:05 Windows 兜底任务的幂等标记，T-20260903-002）
    plan = {
        "date": args.date,
        "executed_live": True,
        "executed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tier": args.top,
        "target": target,
        "strategy_capital": round(capital, 2),
        "target_value_each": round(target_value, 2),
        "sell": sell_plan, "buy": [o for o in exec_buys if o.get("vol")],
        "trim": trim_orders, "hold": hold_plan, "locked": locked,
        "guard_note": guard_note,
    }
    out = os.path.join(os.path.dirname(C.TRADE_LOG), f"rebalance_{args.date}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2, default=str)
    print("    执行结果已保存:", out)
    trader.stop()


if __name__ == "__main__":
    main()
