# coding: utf-8
"""价值小盘V2 日终对账（Project_10）—— xtquant 外部查询，与 V1.3 完全隔离。

背景：账号 67014907 同时承载 P16 V1.3 与 价值小盘V2，两策略各有账本、互不纳管。
V2 在 QMT 端只有内建 _reconcile（写在 strategy_log_v2.txt），无独立外部对账产物，
本脚本补齐「账户真相 vs V2 账本」的外部核对，供 de_guard_runner 兜底检测。

核对：
  1) 基准账本 D:/QMT_POOL/v2_holdings_state.json 的 holdings（校验 account_id 戳，不符 fail-safe）
  2) 账户持仓 query_stock_positions（volume>0）
  3) 差额 = 账户 - 账本（code 一律规范化为 6 位）：
     - >0 且 code 属 V1.3 → cross_strategy（仅 notes，同账号他人持仓，非 V2 孤儿）
     - >0 非 V1.3 → orphans（issues，孤儿/重复建仓，V2 不主动处理仅预警）
     - <0 → issues（账本多于账户 = 卖出未回写/丢失）
  4) 当日 V2 委托 query_stock_orders 过滤 remark 含 "V2" → 汇总 + by_status
  5) V2 内建现金为虚拟子账本口径（账号现金共享），仅记录不比对

用法：
  python reconcile_v2.py                # 今天
  python reconcile_v2.py --date 20261013
输出：data/reconcile_v2/reconcile_v2_<date>.{json,md}
退出码：0=无 issues；1=有 issues（含连接失败/账本缺失）
"""
import argparse
import csv
import json
import os
import sys
import time

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(PROJ_DIR, "data", "reconcile_v2")

ACCOUNT_ID = "67014907"
USERDATA = r"D:\国金QMT交易端模拟\userdata_mini"
STATE_FILE = "D:/QMT_POOL/v2_holdings_state.json"
V13_TRADE_LOG = "d:/QuantLab/projects/Project_16_LightGBM股票大师/data/qmt_trade_log.csv"


def _norm_code(code):
    """规范化为 6 位（兼容 '600000' 与 '600000.SH'）"""
    return str(code or "").strip().split(".")[0]


def _load_ledger():
    """读 V2 账本 holdings。返回 (holdings{6位:vol}, ledger_account_id, cash) 或 (None, None, None)。"""
    if not os.path.exists(STATE_FILE):
        return None, None, None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return None, None, None
    acct = str(st.get("account_id", ""))
    holdings = {}
    for k, v in (st.get("holdings", {}) or {}).items():
        c = _norm_code(k)
        try:
            vol = int(float(v or 0))
        except (TypeError, ValueError):
            vol = 0
        if c and vol > 0:
            holdings[c] = holdings.get(c, 0) + vol
    try:
        cash = float(st.get("cash", 0) or 0)
    except (TypeError, ValueError):
        cash = 0.0
    return holdings, acct, cash


def _v13_hold_codes():
    """从 P16 V1.3 成交日志推导其当前持仓 code 集合（BUY-SELL 净额>0，仅本账号）。
    用于隔离：同账号 67014907 上 V1.3 的持仓对 V2 账本而言是「他人持仓」。"""
    codes = set()
    if not os.path.exists(V13_TRADE_LOG):
        return codes
    net = {}
    try:
        with open(V13_TRADE_LOG, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if str(row.get("account_id", "")).strip() != ACCOUNT_ID:
                    continue
                code = _norm_code(row.get("code", ""))
                side = str(row.get("side", "")).strip().upper()
                try:
                    vol = int(float(row.get("vol", 0) or 0))
                except (TypeError, ValueError):
                    continue
                if not code or vol <= 0:
                    continue
                net[code] = net.get(code, 0) + (vol if side == "BUY" else -vol)
    except Exception:
        return codes
    for c, v in net.items():
        if v > 0:
            codes.add(c)
    return codes


def _query_qmt():
    """连接 67014907，返回 (positions{6位:vol}, orders[])，连接失败返回 (None, None)。"""
    try:
        from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
        from xtquant.xttype import StockAccount
    except Exception as e:
        print("[QMT] import xtquant 失败: %s" % e)
        return None, None
    trader = XtQuantTrader(USERDATA, int(time.time()))
    try:
        trader.register_callback(XtQuantTraderCallback())
    except Exception:
        pass
    try:
        trader.start()
    except Exception as e:
        print("[QMT] start 失败: %s" % e)
        return None, None
    rc = trader.connect()
    if rc != 0:
        print("[QMT] connect 失败 rc=%s" % rc)
        try:
            trader.stop()
        except Exception:
            pass
        return None, None
    acc = StockAccount(ACCOUNT_ID)
    trader.subscribe(acc)
    time.sleep(2)
    positions = {}
    try:
        for p in (trader.query_stock_positions(acc) or []):
            code = _norm_code(getattr(p, "stock_code", "") or "")
            vol = int(getattr(p, "volume", 0) or 0)
            if code and vol > 0:
                positions[code] = positions.get(code, 0) + vol
    except Exception as e:
        print("[QMT] 查询持仓异常: %s" % e)
    orders = []
    try:
        orders = trader.query_stock_orders(acc) or []
    except Exception as e:
        print("[QMT] 查询委托异常: %s" % e)
    return positions, orders


def main():
    ap = argparse.ArgumentParser(description="价值小盘V2 日终对账")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    date = args.date or time.strftime("%Y%m%d")

    report = {
        "date": date, "account_id": ACCOUNT_ID,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "connected": True,
        "ledger_account_id": None, "ledger_cash": None,
        "ledger": {}, "account_positions": {}, "deltas": {},
        "orphans": [], "cross_strategy": [],
        "v13_hold_codes": [], "snapshot_missing": False,
        "orders_v2": [], "orders_v2_by_status": {},
        "issues": [], "notes": [],
    }

    # ① 基准账本
    ledger, ledger_acct, cash = _load_ledger()
    if ledger is None:
        report["issues"].append("V2 账本缺失或不可读: %s" % STATE_FILE)
        ledger = {}
    else:
        report["ledger_account_id"] = ledger_acct
        report["ledger_cash"] = cash
        if ledger_acct != ACCOUNT_ID:
            report["issues"].append(
                "V2 账本账号戳不匹配（账本=%s 本脚本=%s），fail-safe 不比对" % (ledger_acct or "无戳", ACCOUNT_ID))
            ledger = {}
    report["ledger"] = ledger

    # ② 账户持仓 + 当日委托
    acct, orders = _query_qmt()
    if acct is None:
        report["connected"] = False
        report["issues"].append("QMT 连接失败（xtquant 连 %s 失败），无法核对账户持仓" % ACCOUNT_ID)
        acct = {}
    report["account_positions"] = acct

    # ③ 差额对账（含 SNAPSHOT_MISSING guard）
    v13_codes = _v13_hold_codes()
    report["v13_hold_codes"] = sorted(v13_codes)
    # SNAPSHOT_MISSING：休市日 QMT 不产出快照 → acct 空而账本非空，差额全负会误报"卖出未回写"
    snapshot_missing = (not acct) and bool(ledger)
    report["snapshot_missing"] = snapshot_missing
    if snapshot_missing:
        report["notes"].append(
            "SNAPSHOT_MISSING：账户持仓快照为空而账本非空，差额与孤儿判定已跳过，请人工核对上一交易日快照")
    else:
        for code in sorted(set(ledger) | set(acct)):
            diff = acct.get(code, 0) - ledger.get(code, 0)
            if diff:
                report["deltas"][code] = diff
        for code, diff in report["deltas"].items():
            if diff > 0:
                if code in v13_codes:
                    report["cross_strategy"].append({"code": code, "vol": diff,
                        "hint": "账户比 V2 账本多 %d 股（V1.3 持仓，同账号非 V2 孤儿）" % diff})
                else:
                    report["orphans"].append({"code": code, "vol": diff,
                        "hint": "账户比 V2 账本多 %d 股（孤儿/未知，V2 不主动处理，仅预警）" % diff})
                    report["issues"].append("持仓差额: %s 账户比账本多 %d 股" % (code, diff))
            else:
                report["issues"].append("持仓差额: %s 账本比账户多 %d 股（卖出未回写/丢失，需排查）" % (code, -diff))
        if report["orphans"]:
            report["issues"].append("发现 %d 只持仓超额（孤儿/未知），V2 不主动处理，仅预警" % len(report["orphans"]))
        if report["cross_strategy"]:
            # 提示类进 notes（不进 issues，防 de_guard_runner 每日误触发 DE）
            report["notes"].append("账户含 %d 只 V1.3 持仓（同账号，非 V2 孤儿，仅提示）" % len(report["cross_strategy"]))

    # ④ 当日 V2 委托
    v2_orders = []
    by_status = {}
    for o in orders:
        remark = str(getattr(o, "order_remark", "") or "")
        if "V2" not in remark:
            continue
        st = str(getattr(o, "order_status", "") or "")
        by_status[st] = by_status.get(st, 0) + 1
        v2_orders.append({
            "code": _norm_code(getattr(o, "stock_code", "") or ""),
            "order_id": str(getattr(o, "order_id", "") or ""),
            "order_volume": int(getattr(o, "order_volume", 0) or 0),
            "traded_volume": int(getattr(o, "traded_volume", 0) or 0),
            "price": getattr(o, "price", 0) or 0,
            "status": st,
            "remark": remark,
        })
    report["orders_v2"] = v2_orders
    report["orders_v2_by_status"] = by_status

    # 输出
    os.makedirs(OUT_DIR, exist_ok=True)
    jp = os.path.join(OUT_DIR, "reconcile_v2_%s.json" % date)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    mp = os.path.join(OUT_DIR, "reconcile_v2_%s.md" % date)
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# 价值小盘V2 日终对账 %s\n\n" % date)
        f.write("- 账号 %s\n" % ACCOUNT_ID)
        f.write("- 生成时间 %s\n" % report["generated_at"])
        f.write("- QMT 连接 %s\n\n" % ("正常" if report["connected"] else "失败"))
        f.write("## V2 账本 vs 账户持仓（差额=账户-账本）\n")
        if report["snapshot_missing"]:
            f.write("⚠ SNAPSHOT_MISSING：账户快照为空，差额与孤儿判定跳过，请人工核对上一交易日快照。\n")
        else:
            for code in sorted(set(ledger) | set(acct)):
                lv = ledger.get(code, 0)
                av = acct.get(code, 0)
                diff = av - lv
                if diff == 0:
                    flag = "✓"
                elif diff > 0:
                    flag = "⚠ 账户多 %d" % diff
                else:
                    flag = "⚠ 账户少 %d" % -diff
                f.write("- %s V2账本=%d 账户=%d %s\n" % (code, lv, av, flag))
        f.write("\n## 持仓差额/孤儿\n")
        if report["cross_strategy"]:
            f.write("**同账号 V1.3 持仓（非 V2 孤儿）**\n")
            for o in report["cross_strategy"]:
                f.write("- %s %d 股：%s\n" % (o["code"], o["vol"], o["hint"]))
        if report["orphans"]:
            for o in report["orphans"]:
                f.write("- %s %d 股：%s\n" % (o["code"], o["vol"], o["hint"]))
        if not report["cross_strategy"] and not report["orphans"]:
            f.write("- 无\n")
        f.write("\n## 当日 V2 委托（%d 条）\n" % len(v2_orders))
        if by_status:
            for k in sorted(by_status):
                f.write("- %s: %d\n" % (k, by_status[k]))
        else:
            f.write("- 无\n")
        f.write("\n## 提示\n")
        if report["notes"]:
            for n in report["notes"]:
                f.write("- %s\n" % n)
        else:
            f.write("- 无\n")
        f.write("\n## 问题\n")
        if report["issues"]:
            for i in report["issues"]:
                f.write("- ⚠ %s\n" % i)
        else:
            f.write("- 无\n")

    print("对账完成: %s" % jp)
    print("账本=%d 账户=%d 孤儿=%d 交叉=%d V2委托=%d 问题=%d" % (
        len(ledger), len(acct), len(report["orphans"]),
        len(report["cross_strategy"]), len(v2_orders), len(report["issues"])))
    for i in report["issues"]:
        print("  [ISSUE] %s" % i)
    return 1 if report["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
