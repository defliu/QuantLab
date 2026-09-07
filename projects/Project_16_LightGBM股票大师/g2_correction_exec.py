# coding: utf-8
"""一次性 G2 纠错执行（2026-09-04，诚哥拍板）：卖过期数据误选的 600262/300964 → 买最新数据重算的 300475/001266。

背景：600262/300964 系 09-01 候选（当时 moneyflow 缺 11 天，模型 mf_* 特征过期）在 09-02 按 T-1 设计买入；
      最新数据（moneyflow 已刷至 09-02）重算后两者掉出 G2 top2，正确 top2 = 300475 + 001266。
本脚本在交易日 09:35 后用**实时行情**执行：先卖后买，写大QMT 桥 cmd/orders_<date>.json（70180771）。

流程：
  1) 前置守卫：交易日 / 时间>=09:35（实时行情前提）/ 桥存活（live 时）
  2) **开盘后用实时数据重跑 deploy_predict_g2（--threshold 60 --top 2 --pool 100）重算今日 top2**
  3) 卖出 = G2 已知持仓中不在今日 top2 的（600262/300964），量取桥 state/positions 最新文件
  4) 买入 = 今日 top2 中未持有的，等权 95% 池 / N
  5) 实时取价 → 构建 SELL/BUY → write_orders 写桥（--live）
  6) 更新 g2_hold_dates（建仓日=运行日）；不生成 positions_cfg（等 15:40 reconcile 以 QMT 真相为准）
  7) 落盘 data/rebalance_g2/g2_correction_<date>.json + 飞书通知

用法：
  python g2_correction_exec.py                  # dry-run 预览（需 --force-preview 跳过时间守卫）
  python g2_correction_exec.py --live           # 真写桥（09:35 后）
"""
import argparse
import csv
import datetime
import glob
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rebalance_g2 as R
import is_trade_day as ITD

SELL_HINTS = ["600262.SH", "300964.SZ"]  # 过期数据误选票（守卫用：只允许卖这些，防误纳管他票）
TOP2_CSV_DEFAULT = "20260903_g2_top2.csv"


def _latest_positions_file():
    """桥 state/positions_*.json 中最新一份（盘前桥未写当日文件，用最近已知）。"""
    pat = os.path.join(R.G.BRIDGE_DIR, "state", "positions_*.json") if hasattr(R.G, "BRIDGE_DIR") else None
    if not pat:
        return None
    files = sorted(glob.glob(pat))
    return files[-1] if files else None


def _known_volumes():
    """从最新 positions 文件取 600262/300964 的实际持仓量（默认 6400/1000，盘后无成交仍准确）。"""
    fallback = {"600262.SH": 6400, "300964.SZ": 1000}
    try:
        pf = _latest_positions_file()
        if not pf:
            return fallback
        with open(pf, encoding="utf-8") as f:
            d = json.load(f)
        out = {}
        for p in d.get("positions", []):
            c = p.get("code", "")
            if c in SELL_HINTS and int(p.get("volume", 0) or 0) > 0:
                out[c] = int(p.get("volume", 0) or 0)
        return out or fallback
    except Exception as e:
        print("  !! 读取 positions 失败(%s)，回退 6400/1000" % e)
        return fallback


def main():
    ap = argparse.ArgumentParser(description="G2 一次性纠错：卖过期误选 → 买最新 top2（实时行情）")
    ap.add_argument("--live", action="store_true", help="真写桥")
    ap.add_argument("--force-preview", action="store_true", help="非交易时段/未到09:35也允许 dry-run 预览")
    ap.add_argument("--top2-csv", default=None)
    ap.add_argument("--min-open", default="0935", help="实时行情最早时间 HHMM")
    args = ap.parse_args()
    date = R.today_str()
    now = datetime.datetime.now()

    print("== G2 纠错（%s）== 时间 %s" % (date, now.strftime("%Y-%m-%d %H:%M:%S")))

    # 1) 交易日守卫
    try:
        cal = ITD.load_calendar()
        r = ITD.is_trade_day(now.date(), cal)
        if not r.get("is_trade_day"):
            print("[ABORT] 非交易日")
            return 0
    except Exception as e:
        print("[ABORT] 交易日判断失败: %s" % e)
        return 1

    # 2) 时间守卫（实时行情前提）
    if not args.force_preview and now.strftime("%H%M") < args.min_open:
        print("[ABORT] 未到 %s，市场未开盘无实时行情（--force-preview 可预览）。中止" % args.min_open)
        return 1

    # 3) 开盘后用实时数据重跑 deploy_predict_g2，重算今日 top2（G2 策略口径：--threshold 60 --top 2 --pool 100）
    print("  >> 重跑 deploy_predict_g2（实时数据）...")
    deploy_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy_predict_g2.py")
    try:
        dr = subprocess.run([sys.executable, deploy_py, "--threshold", "60", "--top", "2", "--pool", "100"],
                            capture_output=True, text=True, encoding="utf-8", timeout=600)
        if dr.returncode != 0:
            print("[ABORT] deploy_predict_g2 失败（rc=%d），无法确定今日 top2，中止\n%s"
                  % (dr.returncode, (dr.stdout or "")[-500:]))
            return 1
        print("  deploy 完成 rc=0")
    except Exception as e:
        print("[ABORT] deploy_predict_g2 执行异常: %s" % e)
        return 1

    # 4) 读重算后的最新 top2（今日排名）
    sel_dir = R.G.G2_SELECT_DIR
    top2_files = sorted([f for f in os.listdir(sel_dir) if f.endswith("_g2_top2.csv") and f[:8].isdigit()])
    if not top2_files:
        print("[ABORT] 无 _g2_top2.csv（deploy 未产出）")
        return 1
    top2_path = os.path.join(sel_dir, top2_files[-1])
    print("今日 top2 文件: %s" % top2_path)
    if not os.path.exists(top2_path):
        print("[ABORT] 缺少最新 top2 文件: %s" % top2_path)
        return 1
    fresh_top2 = []
    with open(top2_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = (row.get("ts_code") or "").strip()
            if code:
                fresh_top2.append(code)
    fresh_top2 = fresh_top2[:2]
    if len(fresh_top2) != 2:
        print("[ABORT] top2 不足 2 只: %s" % fresh_top2)
        return 1
    print("今日 top2（重算）: %s" % fresh_top2)

    # 5) 卖出 = G2 已知持仓中不在 top2 的（仅限 SELL_HINTS）
    known = _known_volumes()
    sell_targets = {c: v for c, v in known.items() if c not in fresh_top2}
    if any(c not in SELL_HINTS for c in sell_targets):
        print("[ABORT] 卖出清单超出允许范围 %s: %s" % (SELL_HINTS, list(sell_targets)))
        return 1
    print("待卖出（过期误选，今日 top2 之外）: %s" % sell_targets)
    if not sell_targets:
        print("[NOOP] 无待卖持仓")

    # 6) 买入 = top2 中未持有的
    buy_codes = [c for c in fresh_top2 if c not in known]
    capital = R.G.load_g2_capital()
    n_slots = len(buy_codes) or 1
    budget_each = capital * (1 - R.G.RESERVE_CASH_PCT) / float(n_slots)
    print("买入候选: %s | 资金池 %.0f | budget_each %.0f" % (buy_codes, capital, budget_each))

    # 7) 桥存活（live 时强校验）
    alive, msg = R.is_bridge_alive(max_age=900)
    print("桥存活: %s | %s" % (alive, msg))
    if args.live and not alive:
        print("[ABORT] 桥心跳异常，拒绝写单（避免盲单）")
        return 1

    # 8) 实时取价 → 订单
    orders = []
    try:
        seq0 = int(R.read_heart(date).get("last_cmd_seq_processed", 0) or 0)
    except Exception:
        seq0 = 0
    # 先卖
    for code, vol in sell_targets.items():
        price = R.fetch_price(code)
        if not price or price <= 0:
            if args.live:
                print("[ABORT] 卖出 %s 实时取价失败，中止" % code)
                return 1
            print("  [预览] %s 实时取价失败，预览用占位价" % code)
            price = 0.0
        orders.append({"action": "SELL", "code": code, "vol": int(vol), "price": round(price, 3),
                       "reason": "g2纠错(过期moneyflow误选,最新数据掉出top2)",
                       "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1)})
    # 后买
    for i, code in enumerate(buy_codes):
        price = R.fetch_price(code)
        if not price or price <= 0:
            if args.live:
                print("[ABORT] 买入 %s 实时取价失败，中止" % code)
                return 1
            print("  [预览] %s 实时取价失败，预览用占位价" % code)
            price = 0.0
            continue
        vol = int(budget_each / price / R.G.MIN_ORDER_VOL) * R.G.MIN_ORDER_VOL
        if vol <= 0:
            print("  [SKIP] 买入 %s 资金不足一手（%.2f）" % (code, price))
            continue
        orders.append({"action": "BUY", "code": code, "vol": vol, "price": round(price, 3),
                       "reason": "g2纠错Top%d(最新数据)" % (i + 1),
                       "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1)})

    if not orders:
        print("[NOOP] 无订单")
        return 0
    for o in orders:
        print("  %s %s %s %5d股 @%.3f %s" % (o["action"], o["code"], o["strategy_order_id"], o["vol"], o["price"], o["reason"]))

    if not args.live:
        print("DRY-RUN：未写桥。确认后加 --live。")
        return 0

    # 9) 写桥
    seq = R.write_orders(orders, date=date, account_id=R.G.ACCOUNT_ID)
    print("已写 cmd/orders_%s.json seq=%d（%d 条）" % (date, seq, len(orders)))

    # 10) 更新 g2_hold_dates（新买建仓日=运行日；**不移除旧票建仓日**——防 09:50 换仓读旧账本时
    #     因 hold_dates 缺失而误触发 SELL（旧票到期前保持 SKIP，15:40 reconcile 后账本自然切换））
    plan = {"date": date, "account_id": R.G.ACCOUNT_ID, "capital": capital,
            "sells": [],
            "buys": [{"code": o["code"], "vol": o["vol"]} for o in orders if o["action"] == "BUY"]}
    try:
        hd = R._update_hold_dates(plan, date)
        R.save_hold_dates(hd)
        print("g2_hold_dates 已更新: %s" % json.dumps(hd, ensure_ascii=False))
    except Exception as e:
        print("  !! hold_dates 更新失败（不阻断）: %s" % e)

    # 11) 落盘纠错记录
    rec = {"date": date, "account_id": R.G.ACCOUNT_ID, "seq": seq,
           "sells": [{"code": c, "vol": v} for c, v in sell_targets.items()],
           "buys": [{"code": o["code"], "vol": o["vol"], "price": o["price"]} for o in orders if o["action"] == "BUY"],
           "fresh_top2": fresh_top2, "reason": "过期moneyflow(缺11天)误选 600262/300964，最新数据掉出top2；实时行情换入最新top2",
           "written_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    rec_dir = os.path.join(R.G.DATA_DIR, "rebalance_g2")
    os.makedirs(rec_dir, exist_ok=True)
    with open(os.path.join(rec_dir, "g2_correction_%s.json" % date), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    print("纠错记录: %s" % os.path.join(rec_dir, "g2_correction_%s.json" % date))
    try:
        R._notify_feishu("【G2纠错 %s】卖%s→买%s（实时行情 seq=%d）" % (
            date, list(sell_targets.keys()), buy_codes, seq))
    except Exception as e:
        print("  !! 飞书通知失败（不阻断）: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
