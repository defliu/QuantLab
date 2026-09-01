# coding: utf-8
"""G2（大QMT 文件桥）每日换仓执行 —— 与 V1.3（miniQMT 67014907）**完全隔离**。

流程（09:45 开盘 15 分钟后执行，先卖后买）：
  1) 读 g2 选股 data/selections/g2/<date>_g2_top2.csv（deploy_predict_g2 产出，total>=60 已过滤）
  2) 读 G2 账本持仓（只认 G2 自己的：cmd/positions_cfg_<date>.json 成本锚 + fills FIFO 推导；
     绝不读/纳管账户全量持仓里他人的票）
  3) 卖出：G2 账本中不在目标 topN 的 → SELL（可卖量受 can_use 限制，T+1 锁定不卖）
  4) 买入：目标中持有不足的 → BUY 补到目标股数（等权对齐资金池 95%/N，整手）
  5) 写 orders 到桥 cmd/orders_<date>.json（dry-run 默认，--live 才真写）
  6) 输出 data/rebalance_g2_<date>.md/.json

用法：
  python rebalance_g2.py                  # dry-run（默认）
  python rebalance_g2.py --date 20260902  # 指定日期
  python rebalance_g2.py --live           # 真写桥（慎用，先卖后买）
"""
import argparse
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g2_config as G
from qmt_bridge_client import (
    write_orders, read_fills, read_positions, read_heart,
    positions_from_fills, fetch_price, is_bridge_alive, _cmd_path,
)


def today_str():
    return time.strftime("%Y%m%d")


def load_g2_selection(date, top_n):
    """读 g2 选股 CSV → 目标清单（按 total 降序取 topN，total>=REDLINE）。"""
    csv_path = os.path.join(G.G2_SELECT_DIR, "%s_g2_top%d.csv" % (date, top_n))
    if not os.path.exists(csv_path):
        alt = os.path.join(G.DATA_DIR, "selections", "%s_selection_full.csv" % date)
        if os.path.exists(alt):
            csv_path = alt
        else:
            return [], "g2 选股 CSV 不存在: %s" % csv_path
    picks = []
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("ts_code") or "").strip()
            if not code:
                continue
            try:
                total = float(row.get("total_new", row.get("total", 0) or 0))
            except ValueError:
                total = 0.0
            if total < G.REDLINE:
                continue
            picks.append({"code": code, "total": total})
    if not picks:
        return [], "g2 选股无 total>=%.0f 的票（宁缺毋滥）" % G.REDLINE
    picks.sort(key=lambda p: p["total"], reverse=True)
    return picks[:top_n], "g2 选股 %d 只（红线 %.0f）" % (len(picks), G.REDLINE)


def load_g2_ledger(date):
    """G2 账本持仓：positions_cfg（成本锚）为主 + fills FIFO 推导兜底。
    返回 {code: {"vol": int, "cost": float, "src": str}}。绝不读账户全量持仓。"""
    ledger = {}
    # ① positions_cfg（G2 自写成本锚 = 纳管持仓）
    try:
        cfg = json.load(open(_cmd_path_positions_cfg(date), encoding="utf-8"))
        if str(cfg.get("account_id", "")) == G.ACCOUNT_ID:
            for p in cfg.get("positions", []):
                c = p.get("code", "")
                if c:
                    ledger[c] = {"vol": int(p.get("vol", 0) or 0),
                                 "cost": float(p.get("cost", 0) or 0), "src": "cfg"}
    except Exception:
        pass
    # ② fills FIFO 推导（补 cfg 未覆盖的 G2 成交）
    try:
        derived = positions_from_fills(date)
        for c, v in derived.items():
            if c not in ledger or ledger[c]["vol"] <= 0:
                ledger[c] = {"vol": int(v.get("vol", 0) or 0),
                             "cost": float(v.get("cost", 0) or 0), "src": "fills"}
    except Exception:
        pass
    return {c: v for c, v in ledger.items() if v["vol"] > 0}


def _cmd_path_positions_cfg(date):
    return os.path.join(G.CMD_DIR, "positions_cfg_%s.json" % date)


def load_sellable(date, code):
    """从桥账户 positions 取可卖量（T+1 锁定=0）。账户全量 only 用于取 can_use，不据此判定归属。"""
    try:
        pos = read_positions(date) or {}
        for p in pos.get("positions", []):
            if p.get("code") == code:
                return int(p.get("can_use_volume", 0) or 0)
    except Exception:
        pass
    return None


def build_plan(date, capital):
    """生成换仓计划（卖 + 买）。返回 (orders, plan_dict)。"""
    targets, note = load_g2_selection(date, G.TOP_N)
    ledger = load_g2_ledger(date)
    plan = {
        "date": date, "account_id": G.ACCOUNT_ID, "capital": round(capital, 2),
        "targets": targets, "ledger": ledger, "note": note,
        "sells": [], "buys": [], "skips": [],
    }
    orders = []
    seq0 = 0
    try:
        seq0 = int(read_heart(date).get("last_cmd_seq_processed", 0) or 0)
    except Exception:
        pass

    tgt_codes = [t["code"] for t in targets]

    # ---- 卖出：账本中不在目标 → SELL ----
    for code, ld in ledger.items():
        if code in tgt_codes:
            continue
        sellable = load_sellable(date, code)
        vol = ld["vol"]
        if sellable is not None:
            vol = min(vol, sellable)
        if vol <= 0:
            plan["skips"].append({"code": code, "vol": ld["vol"], "reason": "T+1锁定可卖0"})
            continue
        price = fetch_price(code) or ld["cost"]
        orders.append({
            "action": "SELL", "code": code, "vol": vol, "price": round(price, 3),
            "reason": "g2调出(不在目标)", "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
        })
        plan["sells"].append({"code": code, "vol": vol, "cost": ld["cost"], "price": round(price, 3)})

    # ---- 买入：目标等权对齐 ----
    if targets:
        per_budget = capital * (1 - G.RESERVE_CASH_PCT) / float(len(targets))
        for i, t in enumerate(targets, 1):
            code = t["code"]
            held = ledger.get(code, {}).get("vol", 0)
            price = fetch_price(code)
            if not price or price <= 0:
                plan["skips"].append({"code": code, "reason": "取价失败跳过"})
                continue
            target_vol = int(per_budget / price / G.MIN_ORDER_VOL) * G.MIN_ORDER_VOL
            if target_vol <= held:
                plan["skips"].append({"code": code, "vol": held, "reason": "已足额(目标%d股)" % target_vol})
                continue
            need = target_vol - held
            orders.append({
                "action": "BUY", "code": code, "vol": need, "price": round(price, 3),
                "reason": "g2选股Top%d(total=%.1f)" % (i, t["total"]),
                "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
            })
            plan["buys"].append({"code": code, "vol": need, "price": round(price, 3),
                                 "held": held, "target_vol": target_vol, "total": t["total"]})
    return orders, plan


def main():
    ap = argparse.ArgumentParser(description="G2 每日换仓（先卖后买，写大QMT 桥）")
    ap.add_argument("--date", default=None)
    ap.add_argument("--top", type=int, default=G.TOP_N)
    ap.add_argument("--capital", type=float, default=None, help="资金池覆盖（默认读 g2_strategy_capital.json）")
    ap.add_argument("--live", action="store_true", help="真写桥 cmd/orders_<date>.json（缺省 dry-run）")
    args = ap.parse_args()

    date = args.date or today_str()
    G.TOP_N = args.top
    capital = args.capital or G.load_g2_capital()

    alive, msg = is_bridge_alive(max_age=600, date=date)
    print("== G2 换仓（%s）==" % date)
    print("桥存活: %s | %s" % (alive, msg))
    print("资金池: %.0f 元（%s）" % (capital, G.G2_CAPITAL_FILE))

    orders, plan = build_plan(date, capital)
    print(plan["note"])
    if not orders:
        print("[NOOP] 无买卖指令")
        plan["orders"] = []
        _save_plan(plan, date)
        return 0
    for o in orders:
        print("  %s %s %s %5d股 @%.3f %s" % (o["action"], o["code"], o["strategy_order_id"], o["vol"], o["price"], o["reason"]))

    if args.live:
        if not alive:
            print("[ABORT] 桥心跳异常，拒绝写单")
            return 1
        seq = write_orders(orders, date=date, account_id=G.ACCOUNT_ID)
        print("已写 cmd/orders_%s.json seq=%d（%d 条）" % (date, seq, len(orders)))
        plan["seq"] = seq
        plan["written"] = True
    else:
        print("DRY-RUN：未写桥。确认后加 --live。")
        plan["written"] = False
    plan["orders"] = orders
    _save_plan(plan, date)
    return 0


def _save_plan(plan, date):
    out_dir = os.path.join(G.DATA_DIR, "rebalance_g2")
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "rebalance_g2_%s.json" % date)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    mp = os.path.join(out_dir, "rebalance_g2_%s.md" % date)
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# G2 换仓计划 %s\n\n" % date)
        f.write("- 账号 %s | 资金池 %.0f 元 | %s\n\n" % (plan["account_id"], plan["capital"], plan["note"]))
        f.write("## 卖出\n")
        for s in plan.get("sells", []):
            f.write("- SELL %s %d 股（成本 %.3f）\n" % (s["code"], s["vol"], s["cost"]))
        f.write("\n## 买入\n")
        for b in plan.get("buys", []):
            f.write("- BUY %s %d 股 @%.3f（现持 %d → 目标 %d）\n" % (b["code"], b["vol"], b["price"], b["held"], b["target_vol"]))
        f.write("\n## 跳过\n")
        for sk in plan.get("skips", []):
            f.write("- %s：%s\n" % (sk.get("code", "?"), sk.get("reason", "")))
        f.write("\n## 原始指令\n")
        for o in plan.get("orders", []):
            f.write("- %s %s %s %d 股 @%.3f %s\n" % (o["action"], o["code"], o["strategy_order_id"], o["vol"], o["price"], o["reason"]))
    print("计划已存: %s" % jp)


if __name__ == "__main__":
    sys.exit(main())
