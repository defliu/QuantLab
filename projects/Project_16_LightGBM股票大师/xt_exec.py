# coding: utf-8
"""G2 / ENS 共用的「xtquant 直连下单 + 独立账本」执行层（2026-10-22 拍板 C 落地）。

背景
----
2026-10-22 拍板：G2 / ENS 由大QMT 70180771 迁至 miniQMT 67014907，改为外部 Python
经 xtquant 直连下单（原文件桥 cmd/orders_*.json 在 QMT 沙箱下内置侧读不到，已作废）。

本模块只做三件事：
  1) **连接** 67014907（xtquant XtQuantTrader）
  2) **委托** 复用 order_guard.order_with_guard（轮询成交 / 撤单重试 / 涨跌停跳过 /
     成交均价回填 / query_stock_trades 守护线程超时降级，全部经实战事故验证）
  3) **账本** 独立 CSV（g2_trade_log.csv / ens_trade_log.csv），按 BUY-SELL 净额推导持仓，
     绝不与 V1.3 / 另一策略共用文件

红线（AGENTS「资金分配」「实盘执行」）
  - 只动本策略账本内的票；孤儿持仓（账户有、账本无）不纳管、只告警
  - 账本必须内嵌 account_id 戳；戳不符 → 备份旧档 + 空仓起步（fail-safe）
  - 买卖 remark 各策略独立，防同账号交叉纳管
"""
import csv
import json
import os
import time


# ---------------------------------------------------------------- 连接

def connect(cfg):
    """连接 xtquant 并返回 (trader, account)。失败抛 RuntimeError。"""
    from xtquant import xttrader, xttype
    trader = xttrader.XtQuantTrader(cfg.USERDATA, int(time.time()))
    trader.start()
    rc = trader.connect()
    if rc != 0:
        raise RuntimeError("连接 miniQMT 失败 rc=%s（%s）" % (rc, cfg.USERDATA))
    account = xttype.StockAccount(cfg.ACCOUNT_ID)
    sub = trader.subscribe(account)
    if sub != 0:
        # subscribe !=0 说明账号无该通道权限（70180771 即此症状），必须显式失败
        raise RuntimeError("subscribe 失败 rc=%s（账号 %s 可能未开通 miniQMT）" % (sub, cfg.ACCOUNT_ID))
    time.sleep(2)
    return trader, account


def query_positions(trader, account):
    """账户全量持仓 {code: {"vol","can_use","cost","mv"}}。仅 volume>0。

    注意：**只用于取可卖量与实盘核对，绝不据此判定归属**（同账号三策略并存）。
    """
    out = {}
    for p in (trader.query_stock_positions(account) or []):
        code = getattr(p, "stock_code", "") or ""
        vol = int(getattr(p, "volume", 0) or 0)
        if not code or vol <= 0:
            continue
        out[code] = {
            "vol": vol,
            "can_use": int(getattr(p, "can_use_volume", 0) or 0),
            "cost": float(getattr(p, "open_price", 0) or 0),
            "mv": round(float(getattr(p, "market_value", 0) or 0), 2),
        }
    return out


# ---------------------------------------------------------------- 账本

def _ensure_log(cfg):
    path = cfg.TRADE_LOG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=cfg.TRADE_LOG_FIELDS).writeheader()
    return path


def append_trade(cfg, code, side, vol, price, order_id="", reason=""):
    """追加一条成交记录（成交是既成事实，必须落账）。失败只告警不抛。"""
    try:
        path = _ensure_log(cfg)
        remark = cfg.REMARK_BUY if side.upper() == "BUY" else cfg.REMARK_SELL
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "account_id": cfg.ACCOUNT_ID,
            "code": code,
            "side": side.upper(),
            "vol": int(vol),
            "price": round(float(price), 4),
            "order_id": order_id,
            "remark": remark,
            "reason": reason,
        }
        with open(path, "a", newline="", encoding="utf-8-sig") as f:
            csv.DictWriter(f, fieldnames=cfg.TRADE_LOG_FIELDS).writerow(row)
        return row
    except Exception as e:
        print("    !! [账本] %s %s 落账失败（人工补记）: %r" % (code, side, e))
        return None


def load_trades(cfg):
    """读账本全部记录；account_id 戳不符 → 备份旧档 + 返回 []（fail-safe）。

    AGENTS 红线 T-20260823-004：绝不能拿错账号的账本做交易决策。
    """
    path = cfg.TRADE_LOG
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return []
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        print("    !! [账本] 读取失败 %r" % (e,))
        return []
    if not rows:
        return []
    foreign = [r for r in rows if (r.get("account_id") or "").strip() != str(cfg.ACCOUNT_ID)]
    if foreign:
        old = (foreign[0].get("account_id") or "").strip() or "nostamp"
        bak = "%s.bak_acct_%s_%s" % (path, old, time.strftime("%Y%m%d_%H%M%S"))
        try:
            os.replace(path, bak)
        except Exception as e:
            print("    !! [账本] 备份失败 %r（拒绝使用异账号账本）" % (e,))
            return []
        print("    !! [账本] account_id 戳不符（旧=%s，本策略=%s，%d/%d 行）→ 已备份 %s，空仓起步"
              % (old, cfg.ACCOUNT_ID, len(foreign), len(rows), os.path.basename(bak)))
        return []
    return rows


def ledger_from_trades(cfg):
    """按账本 BUY-SELL 净额推导持仓（FIFO 成本）。

    返回 {code: {"vol": int, "cost": float}}，只含净持仓 > 0。
    """
    from collections import deque
    buys = {}
    vols = {}
    for r in load_trades(cfg):
        code = (r.get("code") or "").strip()
        side = (r.get("side") or "").strip().upper()
        if not code or side not in ("BUY", "SELL"):
            continue
        try:
            vol = int(float(r.get("vol") or 0))
            price = float(r.get("price") or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or price <= 0:
            continue
        if side == "BUY":
            buys.setdefault(code, deque()).append((vol, price))
            vols[code] = vols.get(code, 0) + vol
        else:
            left = vol
            if code in buys:
                while left > 0 and buys[code]:
                    v, cp = buys[code][0]
                    take = min(v, left)
                    left -= take
                    buys[code][0] = (v - take, cp)
                    if buys[code][0][0] <= 0:
                        buys[code].popleft()
            vols[code] = vols.get(code, 0) - vol
    out = {}
    for code, dq in buys.items():
        tv = sum(v for v, _ in dq)
        if tv > 0 and vols.get(code, 0) > 0:
            out[code] = {"vol": int(vols[code]), "cost": round(sum(v * c for v, c in dq) / tv, 4)}
    return out


def reconcile(cfg, trader, account):
    """账本 vs 账户持仓对账。

    返回 {"ledger": {...}, "account": {...}, "deltas": {...}, "orphans": [...], "issues": [...]}
      deltas  : 账本-账户 差额（正=账本多，可能风控卖出未回写；负=账户多，可能买入未落账）
      orphans : 账户有、账本完全无记录的票（其他策略的持仓，绝不纳管）
    """
    ledger = ledger_from_trades(cfg)
    account_pos = query_positions(trader, account)
    deltas, issues = {}, []
    for code, ld in ledger.items():
        acc_vol = (account_pos.get(code) or {}).get("vol", 0)
        if ld["vol"] != acc_vol:
            deltas[code] = ld["vol"] - acc_vol
            issues.append("持仓差额 %s：账本 %d / 账户 %d（%s）" % (
                code, ld["vol"], acc_vol,
                "账本多，疑风控卖出未回写" if ld["vol"] > acc_vol else "账户多，疑买入未落账"))
    orphans = sorted(c for c in account_pos if c not in ledger)
    return {"ledger": ledger, "account": account_pos, "deltas": deltas,
            "orphans": orphans, "issues": issues}


# ---------------------------------------------------------------- 委托

def execute_orders(cfg, trader, account, orders, date=""):
    """按 orders 顺序真实下单（先卖后买由调用方保证）。

    orders: [{"action":"SELL"/"BUY", "code", "vol", "price", "reason"}]
    返回 {"records": [...], "failures": [...], "summary": {...}}

    每笔：order_guard.order_with_guard → 以 traded_vol>0 为唯一记账门槛（T-20260930-001：
    禁止 `ok and traded_vol>0` 双重门槛，撤单窗口内全成交但 ok=False 会漏账）→ append_trade。
    """
    import order_guard
    order_guard.configure(cfg, cfg.PENDING_FILL_FILE)

    records, failures = [], []
    for o in orders:
        code = o.get("code", "")
        side = o.get("action", "").upper()
        vol = int(o.get("vol", 0) or 0)
        ref_px = float(o.get("price", 0) or 0)
        reason = o.get("reason", "")
        if not code or side not in ("BUY", "SELL") or vol <= 0:
            failures.append({"code": code, "side": side, "vol": vol, "reason": "INVALID_ORDER"})
            continue
        try:
            r = order_guard.order_with_guard(trader, account, code, side, vol,
                                             ref_px if ref_px > 0 else None, cfg.REMARK_BUY if side == "BUY" else cfg.REMARK_SELL)
        except Exception as e:
            failures.append({"code": code, "side": side, "vol": vol,
                             "reason": "EXC:%r" % (e,)})
            print("    !! %s %s 下单异常: %r" % (side, code, e))
            continue

        traded = int(r.get("traded_vol", 0) or 0)
        fill_px, px_src = order_guard.resolve_fill_price(r, ref_px, code)
        if traded > 0:
            # 成交价缺失时仍照记成交量（成交是既成事实），price=0 作为待补标记
            if px_src == "unresolved":
                order_guard.record_pending_fill(code, side, traded, r.get("order_id", 0),
                                                ref_px, note="exec:%s" % px_src,
                                                order_ids=r.get("order_ids"))
            append_trade(cfg, code, side, traded, fill_px, r.get("order_id", ""), reason)
        rec = {
            "code": code, "side": side, "plan_vol": vol, "traded_vol": traded,
            "ref_price": ref_px, "fill_price": fill_px, "price_src": px_src,
            "order_id": r.get("order_id", ""), "order_ids": r.get("order_ids", []),
            "action": r.get("action", ""), "ok": bool(r.get("ok")),
            "attempts": r.get("attempts", 0), "note": r.get("note", ""), "reason": reason,
        }
        records.append(rec)
        if traded <= 0:
            failures.append({"code": code, "side": side, "vol": vol,
                             "reason": "%s:%s" % (r.get("action", ""), r.get("note", ""))})
        print("    %s %s %d股 成交%d @%.4f(%s) %s" % (
            side, code, vol, traded, fill_px, px_src, r.get("action", "")))
    return {"records": records, "failures": failures,
            "summary": {"n": len(records), "traded": sum(x["traded_vol"] for x in records),
                        "failures": len(failures)}}


def save_execution(path, cfg, date, result, extra=None):
    """执行结果落盘（JSON），供审计与次日对账。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {
        "date": date,
        "account_id": cfg.ACCOUNT_ID,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "records": result.get("records", []),
        "failures": result.get("failures", []),
        "summary": result.get("summary", {}),
    }
    if extra:
        doc.update(extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path
