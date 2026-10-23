# coding: utf-8
"""Project_10 价值小盘V2 —— 清仓工具（两阶段：preview 只读预演 / execute 真实下单）

背景（2026-10-22 拍板 D）：
  miniQMT 账号 67014907 将转给 V1.3 / G2 / ENS 三策略使用，V2 的 28 只持仓需清空退出。
  拍板要求：**必须做好交易数据留存**（卖出前/中/后全链可审计）。

留存产物（data/archive/）：
  v2_liquidation_before_<date>.json     卖出前 28 只持仓快照 + 逐只盈亏预估
  v2_liquidation_pre_orders_<date>.csv  卖出前当日委托基线（若有）
  v2_liquidation_orders_<date>.csv      每笔委托的下单/反查记录（execute 模式）
  v2_liquidation_trades_<date>.csv      成交明细（execute 模式）
  v2_liquidation_after_<date>.json      卖出后持仓快照（execute 模式）
  v2_liquidation_report_<date>.md       清仓汇总报告（execute 模式）

用法：
  python v2_liquidation.py                    # preview（默认，只读，不下单）
  python v2_liquidation.py --execute --confirm # 真实清仓（需双参数，缺一不下单）

安全：
  - 默认 preview，绝不下单
  - --execute 必须同时给 --confirm
  - 只卖不买；只动 V2 账本内的票；孤儿持仓（账户有、账本无）默认跳过并告警
  - 逐笔反查 order_id，失败的记录下来供人工处理
"""
import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ARCHIVE = os.path.join(HERE, "data", "archive")
STATE_FILE = r"D:\QMT_POOL\v2_holdings_state.json"
QMT_USERDATA = r"D:\国金QMT交易端模拟\userdata_mini"
ACCOUNT_ID = "67014907"

# 限价下浮比例：小额卖单（100-300 股）盘中 -1% 基本即时成交；
# 未成交由 execute 阶段的短轮询发现并记录，不自动追价（保守）
SELL_PRICE_DISCOUNT = 0.99
ORDER_LOOKUP_RETRIES = 6
ORDER_LOOKUP_INTERVAL = 0.3


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def ensure_archive():
    os.makedirs(ARCHIVE, exist_ok=True)


def load_v2_state():
    """读 V2 账本（entry_prices 为买入成本价）。"""
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("[WARN] 读 V2 账本失败: %r" % (e,))
        return {}


def connect():
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(QMT_USERDATA, int(time.time()))
    trader.start()
    rc = trader.connect()
    if rc != 0:
        raise RuntimeError("连接 miniQMT 失败 rc=%s" % rc)
    account = xttype.StockAccount(ACCOUNT_ID)
    sub = trader.subscribe(account)
    print("[connect] account=%s connect=%s subscribe=%s" % (ACCOUNT_ID, rc, sub))
    time.sleep(3)
    return trader, account


def query_positions(trader, account):
    """返回 [{code, vol, can_use, cost, market_value}]，只保留 volume>0。"""
    rows = []
    for p in (trader.query_stock_positions(account) or []):
        code = getattr(p, "stock_code", "") or ""
        vol = int(getattr(p, "volume", 0) or 0)
        if not code or vol <= 0:
            continue
        rows.append({
            "code": code,
            "vol": vol,
            "can_use": int(getattr(p, "can_use_volume", 0) or 0),
            "cost": float(getattr(p, "open_price", 0) or 0),
            "market_value": round(float(getattr(p, "market_value", 0) or 0), 2),
        })
    rows.sort(key=lambda x: x["code"])
    return rows


def fetch_prices(codes):
    """批量取最新价（xtdata get_full_tick），失败返回空 dict 由下游兜底。"""
    if not codes:
        return {}
    try:
        from xtquant import xtdata
        ticks = xtdata.get_full_tick(list(codes)) or {}
        out = {}
        for c, t in ticks.items():
            px = t.get("lastPrice") or 0
            if px and px > 0:
                out[c] = float(px)
        return out
    except Exception as e:
        print("[WARN] 取价失败: %r" % (e,))
        return {}


def dump_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def build_plan(positions, entry_prices, prices):
    """合成清仓计划：每只票的 vol / 成本 / 现价 / 预估盈亏。"""
    plan = []
    for p in positions:
        code = p["code"]
        cost = p["cost"] or float(entry_prices.get(code, 0) or 0)
        px = prices.get(code, 0.0)
        vol = p["vol"]
        cost_amt = round(cost * vol, 2)
        mkt_amt = round(px * vol, 2) if px else 0.0
        pnl = round(mkt_amt - cost_amt, 2) if px else None
        pnl_pct = round((px / cost - 1) * 100, 2) if (px and cost) else None
        plan.append({
            "code": code,
            "vol": vol,
            "can_use": p["can_use"],
            "sellable": p["can_use"] >= vol,
            "cost": round(cost, 4),
            "cost_amount": cost_amt,
            "last_price": round(px, 3) if px else None,
            "market_amount": mkt_amt,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "in_v2_ledger": code in entry_prices,
        })
    return plan


def print_plan(plan):
    print("")
    print("=" * 108)
    print("V2 清仓预演清单（%s）  账号 %s" % (now_str(), ACCOUNT_ID))
    print("=" * 108)
    hdr = "%-12s %6s %6s %9s %12s %9s %12s %9s %8s"
    print(hdr % ("代码", "总量", "可卖", "成本价", "成本金额", "现价", "市值", "盈亏", "盈亏%"))
    print("-" * 108)
    tot_cost = tot_mkt = 0.0
    for r in plan:
        print(hdr % (
            r["code"], r["vol"], r["can_use"], "%.4f" % r["cost"], "%.2f" % r["cost_amount"],
            ("%.3f" % r["last_price"]) if r["last_price"] else "-",
            "%.2f" % r["market_amount"],
            ("%+.2f" % r["pnl"]) if r["pnl"] is not None else "-",
            ("%+.2f%%" % r["pnl_pct"]) if r["pnl_pct"] is not None else "-",
        ))
        tot_cost += r["cost_amount"]
        tot_mkt += r["market_amount"]
    print("-" * 108)
    print("%-12s %6d %6s %9s %12.2f %9s %12.2f %+9.2f %8s" % (
        "合计", len(plan), "", "", tot_cost, "", tot_mkt, tot_mkt - tot_cost,
        ("%+.2f%%" % ((tot_mkt / tot_cost - 1) * 100)) if tot_cost else "-"))
    print("=" * 108)
    not_in = [r["code"] for r in plan if not r["in_v2_ledger"]]
    if not_in:
        print("[WARN] 以下 %d 只不在 V2 账本内（孤儿），execute 模式将跳过: %s"
              % (len(not_in), ", ".join(not_in)))
    unsellable = [r["code"] for r in plan if not r["sellable"]]
    if unsellable:
        print("[WARN] 以下 %d 只可卖量不足（T+1 锁定），只能部分卖出: %s"
              % (len(unsellable), ", ".join(unsellable)))


def save_before_snapshot(plan, date):
    ensure_archive()
    path = os.path.join(ARCHIVE, "v2_liquidation_before_%s.json" % date)
    doc = {
        "date": date,
        "account_id": ACCOUNT_ID,
        "generated_at": now_str(),
        "mode": "preview",
        "count": len(plan),
        "total_cost": round(sum(r["cost_amount"] for r in plan), 2),
        "total_market": round(sum(r["market_amount"] for r in plan), 2),
        "positions": plan,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("[留存] %s" % path)
    return path


def _query_trades_with_timeout(trader, account, timeout=8):
    """query_stock_trades 守护线程超时包裹（2026-10-22/23 实测该接口在 67014907 上 HANG）。

    绝不阻塞清仓流程；超时返回 None，调用方改用 orders 的 traded_volume/traded_price 还原成交。
    """
    import threading
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
        print("  !! query_stock_trades 阻塞 >%ds，跳过（改用 orders 口径）" % timeout)
        return None
    v = box[0] if box else None
    return v if isinstance(v, list) else None


def save_pre_orders(trader, account, date):
    """卖出前当日委托基线（QMT 只保留当日，先落盘）。"""
    ensure_archive()
    try:
        orders = trader.query_stock_orders(account) or []
    except Exception as e:
        print("[WARN] 查当日委托失败: %r" % (e,))
        return
    trades = _query_trades_with_timeout(trader, account)
    rows = []
    for o in orders:
        rows.append({
            "kind": "ORDER",
            "code": getattr(o, "stock_code", ""),
            "order_id": getattr(o, "order_id", ""),
            "order_type": getattr(o, "order_type", ""),
            "status": getattr(o, "order_status", ""),
            "volume": getattr(o, "order_volume", ""),
            "traded_volume": getattr(o, "traded_volume", ""),
            "traded_price": getattr(o, "traded_price", ""),
            "time": getattr(o, "order_time", ""),
            "remark": getattr(o, "order_remark", ""),
        })
    for t in (trades or []):
        rows.append({
            "kind": "TRADE",
            "code": getattr(t, "stock_code", ""),
            "order_id": getattr(t, "order_id", ""),
            "order_type": getattr(t, "order_type", ""),
            "status": "",
            "volume": getattr(t, "traded_volume", ""),
            "traded_volume": getattr(t, "traded_volume", ""),
            "traded_price": getattr(t, "traded_price", ""),
            "time": getattr(t, "traded_time", ""),
            "remark": getattr(t, "order_remark", ""),
        })
    if not trades:
        # trades 接口不可用 → 从 orders 的 traded_volume/traded_price 还原（口径等价）
        for o in orders:
            tv = int(getattr(o, "traded_volume", 0) or 0)
            if tv <= 0:
                continue
            rows.append({
                "kind": "TRADE",
                "code": getattr(o, "stock_code", ""),
                "order_id": getattr(o, "order_id", ""),
                "order_type": getattr(o, "order_type", ""),
                "status": getattr(o, "order_status", ""),
                "volume": tv,
                "traded_volume": tv,
                "traded_price": getattr(o, "traded_price", ""),
                "time": getattr(o, "order_time", ""),
                "remark": getattr(o, "order_remark", ""),
            })
    path = os.path.join(ARCHIVE, "v2_liquidation_pre_orders_%s.csv" % date)
    dump_csv(path, rows, ["kind", "code", "order_id", "order_type", "status",
                          "volume", "traded_volume", "traded_price", "time", "remark"])
    print("[留存] %s（orders=%d trades=%d）" % (path, len(orders), len(trades or [])))


def execute_sell(trader, account, plan, date):
    """逐笔限价卖出，反查 order_id，记录每笔结果。返回 (records, failures)。"""
    from xtquant import xtconstant
    records = []
    failures = []
    for r in plan:
        code = r["code"]
        if not r["in_v2_ledger"]:
            failures.append({"code": code, "reason": "NOT_IN_V2_LEDGER_SKIPPED"})
            continue
        vol = r["can_use"]
        if vol <= 0:
            failures.append({"code": code, "reason": "CAN_USE_ZERO"})
            continue
        px = r["last_price"] or 0
        if px <= 0:
            failures.append({"code": code, "reason": "NO_PRICE"})
            continue
        limit_px = round(px * SELL_PRICE_DISCOUNT, 2)
        try:
            oid = trader.order_stock(
                account, code, xtconstant.STOCK_SELL, vol,
                xtconstant.FIX_PRICE, limit_px, "v2_liquidation", "v2_exit")
        except Exception as e:
            failures.append({"code": code, "reason": "ORDER_EXC:%r" % (e,)})
            print("  !! %s 下单异常: %r" % (code, e))
            continue

        # 反查 order_id（QMT 约 100ms 分配延迟）
        found = None
        for _ in range(ORDER_LOOKUP_RETRIES):
            time.sleep(ORDER_LOOKUP_INTERVAL)
            try:
                for o in (trader.query_stock_orders(account) or []):
                    if (getattr(o, "stock_code", "") == code
                            and getattr(o, "order_id", 0) == oid):
                        found = o
                        break
            except Exception:
                pass
            if found:
                break

        rec = {
            "time": now_str(), "code": code, "side": "SELL", "vol": vol,
            "ref_price": px, "limit_price": limit_px,
            "ret_code": oid,
            "order_id": getattr(found, "order_id", "") if found else "",
            "order_status": getattr(found, "order_status", "") if found else "",
            "traded_volume": getattr(found, "traded_volume", "") if found else "",
            "traded_price": getattr(found, "traded_price", "") if found else "",
            "remark": "v2_exit",
        }
        records.append(rec)
        print("  SELL %s %d股 现价%.3f 限价%.2f ret=%s order_id=%s"
              % (code, vol, px, limit_px, oid, rec["order_id"] or "LOOKUP_FAIL"))
        if not found:
            failures.append({"code": code, "reason": "LOOKUP_FAIL_but_order_sent", "ret": oid})
        time.sleep(0.3)
    return records, failures


def save_execute_artifacts(plan, records, failures, date, trader, account):
    ensure_archive()
    # 委托记录
    dump_csv(os.path.join(ARCHIVE, "v2_liquidation_orders_%s.csv" % date), records,
             ["time", "code", "side", "vol", "ref_price", "limit_price", "ret_code",
              "order_id", "order_status", "traded_volume", "traded_price", "remark"])
    # 成交明细：query_stock_trades 在 67014907 会 HANG（2026-10-22/23 实测），
    # 一律从 orders 的 traded_volume/traded_price 还原（口径等价，且不阻塞）。
    time.sleep(3)
    trades_rows = []
    try:
        for o in (trader.query_stock_orders(account) or []):
            if getattr(o, "order_remark", "") != "v2_exit":
                continue
            vol = int(getattr(o, "traded_volume", 0) or 0)
            px = float(getattr(o, "traded_price", 0) or 0)
            if vol > 0 and px > 0:
                trades_rows.append({
                    "code": getattr(o, "stock_code", ""),
                    "order_id": getattr(o, "order_id", ""),
                    "vol": vol, "price": px,
                    "amount": round(vol * px, 2),
                    "time": getattr(o, "order_time", ""),
                })
    except Exception as e:
        print("[WARN] 查成交（orders 口径）失败: %r" % (e,))
    dump_csv(os.path.join(ARCHIVE, "v2_liquidation_trades_%s.csv" % date), trades_rows,
             ["code", "order_id", "vol", "price", "amount", "time"])
    # 卖出后持仓
    after = query_positions(trader, account)
    with open(os.path.join(ARCHIVE, "v2_liquidation_after_%s.json" % date),
              "w", encoding="utf-8") as f:
        json.dump({"date": date, "account_id": ACCOUNT_ID, "generated_at": now_str(),
                   "remaining_positions": after, "count": len(after)},
                  f, ensure_ascii=False, indent=2)
    print("[留存] orders / trades / after 三件已落盘")
    return trades_rows, after


def write_report(plan, records, failures, trades_rows, after, date):
    ensure_archive()
    path = os.path.join(ARCHIVE, "v2_liquidation_report_%s.md" % date)
    traded_map = {t["code"]: t for t in trades_rows}
    total_pnl = 0.0
    lines = [
        "# V2 清仓报告 %s" % date,
        "",
        "> 账号：%s　生成时间：%s" % (ACCOUNT_ID, now_str()),
        "",
        "## 一、逐只明细",
        "",
        "| 代码 | 卖出量 | 成本价 | 卖出均价 | 成本金额 | 卖出金额 | 盈亏 | 盈亏% | 状态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in plan:
        t = traded_map.get(r["code"])
        if t and t["price"]:
            sell_amt = t["amount"]
            pnl = round(sell_amt - r["cost_amount"], 2)
            pnl_pct = round((t["price"] / r["cost"] - 1) * 100, 2) if r["cost"] else None
            total_pnl += pnl
            status = "已成交"
            px_txt = "%.4f" % t["price"]
            amt_txt = "%.2f" % sell_amt
            pnl_txt = "%+.2f" % pnl
            pct_txt = ("%+.2f%%" % pnl_pct) if pnl_pct is not None else "-"
        else:
            status = "未成交/查询失败"
            px_txt = amt_txt = pnl_txt = pct_txt = "-"
        lines.append("| %s | %d | %.4f | %s | %.2f | %s | %s | %s | %s |" % (
            r["code"], r["vol"], r["cost"], px_txt, r["cost_amount"], amt_txt,
            pnl_txt, pct_txt, status))
    lines += [
        "",
        "## 二、汇总",
        "",
        "- 计划清仓：**%d 只**" % len(plan),
        "- 已成交：**%d 只**" % len(traded_map),
        "- 持仓成本合计：**%.2f 元**" % sum(r["cost_amount"] for r in plan),
        "- 实际卖出金额：**%.2f 元**" % sum(t["amount"] for t in trades_rows),
        "- 已实现盈亏：**%+.2f 元**" % total_pnl,
        "- 剩余持仓：**%d 只**" % len(after),
        "",
        "## 三、异常",
        "",
    ]
    if failures:
        for f in failures:
            lines.append("- `%s`：%s" % (f.get("code"), f.get("reason")))
    else:
        lines.append("- 无")
    lines += ["", "## 四、留存产物", "",
              "- `v2_liquidation_before_%s.json`：卖出前持仓快照" % date,
              "- `v2_liquidation_pre_orders_%s.csv`：卖出前当日委托基线" % date,
              "- `v2_liquidation_orders_%s.csv`：委托记录" % date,
              "- `v2_liquidation_trades_%s.csv`：成交明细" % date,
              "- `v2_liquidation_after_%s.json`：卖出后持仓快照" % date,
              "- `v2_liquidation_report_%s.md`：本报告" % date, ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[留存] %s" % path)


def _in_trading_session(now=None):
    """是否在 A 股连续竞价时段（09:30-11:30 / 13:00-14:57）。

    T-20261023-001（血的教训）：2026-10-22 15:18 收盘后下单，模拟端仍返回
    status=56 / traded_volume=全量，但次日账户"复活" 20 只 → 收盘后委托不可信。
    故真实下单（--execute）必须校验时段，收盘后一律拒绝。
    14:57 之后为收盘集合竞价（市价单易废单），按"不可下单"处理。
    """
    if now is None:
        now = time.localtime()
    h, m = now.tm_hour, now.tm_min
    hm = h * 100 + m
    return (930 <= hm <= 1130) or (1300 <= hm <= 1457)


def main():
    ap = argparse.ArgumentParser(description="V2 清仓（默认 preview 只读）")
    ap.add_argument("--execute", action="store_true", help="真实下单（需同时给 --confirm）")
    ap.add_argument("--confirm", action="store_true", help="二次确认，与 --execute 同时给才生效")
    ap.add_argument("--force-offhours", action="store_true",
                    help="【危险】强制在非交易时段下单（默认拒绝，仅供极端场景人工兜底）")
    args = ap.parse_args()

    date = time.strftime("%Y%m%d")
    state = load_v2_state()
    entry_prices = state.get("entry_prices", {}) or {}
    print("[state] V2 账本持仓 %d 只，cash=%.2f" % (
        len(state.get("holdings", {}) or {}), float(state.get("cash", 0) or 0)))

    trader, account = connect()
    try:
        positions = query_positions(trader, account)
        print("[query] 账户实际持仓 %d 只" % len(positions))
        prices = fetch_prices([p["code"] for p in positions])
        print("[price] 取到 %d/%d 只最新价" % (len(prices), len(positions)))

        plan = build_plan(positions, entry_prices, prices)
        print_plan(plan)
        save_before_snapshot(plan, date)
        save_pre_orders(trader, account, date)

        if not (args.execute and args.confirm):
            print("")
            print("[PREVIEW] 只读预演完成，未下任何单。")
            print("[PREVIEW] 确认清单无误后执行：python v2_liquidation.py --execute --confirm")
            return 0

        # ★ 交易时段闸（T-20261023-001）：收盘后下单在模拟端会"假成交"，次日账户回滚
        if not _in_trading_session():
            print("")
            print("[ABORT] 当前 %s 不在 A 股连续竞价时段（09:30-11:30 / 13:00-14:57），拒绝下单。"
                  % time.strftime("%Y-%m-%d %H:%M:%S"))
            print("[ABORT] 原因：2026-10-22 15:18 收盘后下单 → 模拟端返回 status=56，"
                  "但次日账户复活 20 只（T-20261023-001）。")
            print("[ABORT] 如需强制执行（极不推荐）：加 --force-offhours")
            if not args.force_offhours:
                return 2
            print("[WARN] --force-offhours 已授权，继续下单（后果自负）")
        else:
            print("[时段] 当前 %s 在交易时段内，允许下单" % time.strftime("%H:%M:%S"))

        print("")
        print("[EXECUTE] 开始真实清仓，共 %d 只..." % len(plan))
        records, failures = execute_sell(trader, account, plan, date)
        trades_rows, after = save_execute_artifacts(plan, records, failures, date, trader, account)
        write_report(plan, records, failures, trades_rows, after, date)
        print("")
        print("[DONE] 已委托 %d 笔，失败 %d 笔，剩余持仓 %d 只"
              % (len(records), len(failures), len(after)))
        return 0
    finally:
        try:
            trader.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
