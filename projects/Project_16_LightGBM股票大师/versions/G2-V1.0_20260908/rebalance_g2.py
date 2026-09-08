# coding: utf-8
"""G2（大QMT 文件桥）每日换仓执行 —— 与 V1.3（miniQMT 67014907）**完全隔离**。

流程（09:45 开盘 15 分钟后执行，先卖后买）：
  1) 读 g2 选股 data/selections/g2/<date>_g2_top10.csv（deploy_predict_g2 --top 10 产出，total>=60 已过滤）
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
import datetime
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g2_config as G
import is_trade_day as ITD
import position_filter as PF
from qmt_bridge_client import (
    write_orders, read_fills, read_positions, read_heart,
    positions_from_fills, fetch_price, is_bridge_alive, _cmd_path, notify_feishu,
)


def today_str():
    return time.strftime("%Y%m%d")


def _count_trade_days(start, end):
    """数 start(含) 到 end(不含) 之间的交易日数（start<end）。用 is_trade_day 判断每个自然日。
    主库日历快照可能滞后（如只到 8/20），is_trade_day 对未覆盖日期走「默认交易日 + 节假日表」fallback，
    故 9 月后周末/中秋(9/25-27)/国庆(10/1-7) 正确排除、其余按交易日计。返回 (days, cal_ok)。"""
    try:
        cal = ITD.load_calendar()
    except Exception:
        cal = set()
    try:
        d0 = datetime.datetime.strptime(start, "%Y%m%d").date()
        d1 = datetime.datetime.strptime(end, "%Y%m%d").date()
    except Exception:
        return 0, False
    if d1 <= d0:
        return 0, True
    days = 0
    cur = d0
    while cur < d1:
        r = ITD.is_trade_day(cur, cal)
        if r.get("is_trade_day"):
            days += 1
        cur += datetime.timedelta(days=1)
    return days, bool(cal)


def load_hold_dates():
    """读持仓建仓日 {code(bridge格式): "YYYYMMDD"}。文件缺失/损坏返回 {}。"""
    try:
        if os.path.exists(G.HOLD_DATES_FILE):
            with open(G.HOLD_DATES_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return d.get("hold_dates", {}) or {}
    except Exception:
        pass
    return {}


def save_hold_dates(hold_dates):
    """原子写持仓建仓日 {code: "YYYYMMDD"}。"""
    os.makedirs(os.path.dirname(G.HOLD_DATES_FILE), exist_ok=True)
    payload = {
        "account_id": G.ACCOUNT_ID,
        "strategy": G.STRATEGY,
        "hold_dates": hold_dates,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = G.HOLD_DATES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, G.HOLD_DATES_FILE)


def _update_hold_dates(plan, date):
    """根据当日换仓计划更新持仓建仓日：
    - 新买入（BUY 指令，且此前未持有/已清仓）→ 建仓日=date（运行日）
    - 被卖出（SELL 指令）→ 移除（清仓）
    - 既未买也未卖、仍在持仓 → 保留原建仓日（不重复记账）
    返回更新后的 hold_dates dict（仅 live 时落盘）。"""
    hd = load_hold_dates()
    # 卖出清仓移除
    for s in plan.get("sells", []):
        hd.pop(s.get("code", ""), None)
    # 买入（可能补仓也视为重新建仓；对已有持仓的加仓不覆盖原建仓日，保持最早建仓日语义）
    for b in plan.get("buys", []):
        code = b.get("code", "")
        if code and code not in hd:
            hd[code] = date
    # 保留活跃持仓中既有建仓日（load_g2_ledger 的持仓未动者自然保留）
    return hd


def _gen_positions_cfg(date):
    """生成当日 positions_cfg 成本锚（桥内止损/对账账本依赖，T-20260902-005 自动化）。
    复用 gen_positions_cfg_g2.py 逻辑：G2 持仓 code（g2_hold_dates.json）∩ 账户持仓
    （state/positions_<date>.json avg_price=含费成本）→ 写 cmd/positions_cfg_<date>.json。
    返回退出码（0=已写/无变化；1=异常）。"""
    try:
        import gen_positions_cfg_g2 as GPC
        return GPC.main_with_date(date)
    except Exception as e:
        print("[positions_cfg] 生成异常: %s" % e)
        return 1


def _fetch_ohlc(code):
    """腾讯行情取 现价/昨收/今开/当日成交量(手)。失败返回 None（G2-V1.0 低开校验用）。"""
    try:
        import urllib.request
        sym = code.split(".")[0]
        ex = code.split(".")[1].lower()
        url = "http://qt.gtimg.cn/q=%s%s" % (ex, sym)
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        if "~" in raw:
            f = raw.split("~")
            if len(f) > 6:
                return {"price": float(f[3]), "pre_close": float(f[4]),
                        "open": float(f[5]), "vol_lot": float(f[6])}
    except Exception:
        pass
    return None


def _recent_avg_vol(code, n=5):
    """增量库取该股最近 n 个交易日平均成交量（手）；数据不足/失败返回 None。"""
    try:
        incr = os.path.join(G.PROJECT_DIR, "data_live", "incremental_daily.parquet")
        if not os.path.exists(incr):
            return None
        d = pd.read_parquet(incr, columns=["ts_code", "trade_date", "volume"])
        d["ts_code"] = d["ts_code"].astype(str)
        sub = d[d["ts_code"] == code].sort_values("trade_date")
        if len(sub) < 3:
            return None
        return float(sub["volume"].tail(n).mean())
    except Exception:
        return None


def _notify_feishu(text):
    """G2 飞书文本通知（复用 qmt_bridge_client.notify_feishu，bot 身份+回退本地 config）。"""
    return notify_feishu(text)


def _latest_incr_date():
    """增量库最新交易日（data_live/update_meta.json incremental_range[1]）；读不到返回 None。

    G2 候选由 deploy_predict_g2 基于增量库最新日生成，故用此值做候选新鲜度基准（P0-1，2026-09-02）。
    """
    try:
        meta = os.path.join(os.path.dirname(G.DATA_DIR), "data_live", "update_meta.json")
        with open(meta, encoding="utf-8") as f:
            d = json.load(f)
        rng = d.get("incremental_range") or []
        if len(rng) == 2:
            return str(rng[1])
    except Exception:
        pass
    return None


def _latest_avail_candidate(date, top_n):
    """扫描 g2/ 目录取最新数据日的候选；新鲜度须 == 增量库最新日，否则宁缺毋滥中止。

    候选文件以「数据日」命名（deploy_predict_g2 --top 10 输出，如 20260902_g2_top10.csv），
    而调用方传「运行日」（如 20260902）。运行日候选缺失属正常（候选天然滞后一天），
    自动回退最新数据日候选；但若其数据日落后于增量库最新日，说明 09:25 候选生成失败，
    绝不用过期候选交易。同时移除原逻辑回退 V1.3 selection_full.csv 的违规路径（隔离红线）。
    返回 (csv_path, note)；无可用候选返回 (None, 原因)。
    """
    sel = G.G2_SELECT_DIR
    if not os.path.isdir(sel):
        return None, "g2 选股目录不存在: %s" % sel
    pat = "_g2_top%d.csv" % top_n
    cands = []
    for fn in os.listdir(sel):
        if fn.endswith(pat) and fn[:8].isdigit():
            cands.append((fn[:8], os.path.join(sel, fn)))
    if not cands:
        return None, "g2 选股 CSV 不存在: %s" % os.path.join(sel, "%s_g2_top%d.csv" % (date, top_n))
    cands.sort()
    d, path = cands[-1]
    latest_incr = _latest_incr_date()
    if latest_incr and d != latest_incr:
        return None, ("g2 候选数据日 %s 落后于增量库最新日 %s（09:25 候选生成失败？），"
                      "宁缺毋滥中止，绝不用过期候选" % (d, latest_incr))
    return path, "运行日候选缺失，回退到最新数据日 %s 的候选" % d


def load_g2_selection(date, top_n):
    """读 g2 选股 CSV → 候选池（total>=REDLINE，按 total 降序；不截断，买入侧自行排除持仓取最高）。

    先按运行日 <date> 精确匹配；缺失则回退最新数据日候选（见 _latest_avail_candidate）。
    绝不回退 V1.3 的 selection_full.csv（G2/V1.3 隔离红线）。
    """
    csv_path = os.path.join(G.G2_SELECT_DIR, "%s_g2_top%d.csv" % (date, top_n))
    note = "g2 候选池 %d 只（红线 %.0f）" % (top_n, G.REDLINE)
    if not os.path.exists(csv_path):
        csv_path, note = _latest_avail_candidate(date, top_n)
        if csv_path is None:
            return [], note
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
    return picks, "g2 候选池 %d 只（红线 %.0f）" % (len(picks), G.REDLINE)


def load_g2_ledger(date):
    """G2 账本持仓：positions_cfg（成本锚）为主 + fills FIFO 推导兜底。
    返回 {code: {"vol": int, "cost": float, "src": str}}。绝不读账户全量持仓。"""
    ledger = {}
    # ① positions_cfg（G2 自写成本锚 = 纳管持仓）
    #    当日缺失 → 回退最近可用 positions_cfg（T-20260904 修复：09:50 换仓时当日 positions_cfg 由
    #    15:40 reconcile 才生成；若缺失且 fills 未落盘 → 账本空 → 误判"无持仓"而错误买入 → 回退昨日账本）
    cfg_path = _cmd_path_positions_cfg(date)
    if not os.path.exists(cfg_path):
        cfg_files = sorted([f for f in os.listdir(G.CMD_DIR)
                            if f.startswith("positions_cfg_") and f.endswith(".json")])
        cfg_path = os.path.join(G.CMD_DIR, cfg_files[-1]) if cfg_files else None
        if cfg_path:
            print("  [ledger] 当日 positions_cfg 缺失，回退最近账本 %s" % os.path.basename(cfg_path))
    try:
        if cfg_path:
            cfg = json.load(open(cfg_path, encoding="utf-8"))
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
    """生成换仓计划（卖 + 买）。对齐回测 simulate 语义（scan_rotate_cost_real）：
    - 卖出：持仓满 HOLD_DAYS 到期 → SELL（不看是否在候选池内；止损/止盈由桥内风控处理）
    - 买入：持仓数 < TOP_N 时，从候选池（Top10）选 total_new 最高、不在持仓、过红线的补足（while len(hold) < TOP）
    返回 (orders, plan_dict)。"""
    pool, note = load_g2_selection(date, G.SELECT_TOP)   # 候选池（Top10）
    ledger = load_g2_ledger(date)
    hold_dates = load_hold_dates()   # {code: "YYYYMMDD"} 持仓建仓日
    plan = {
        "date": date, "account_id": G.ACCOUNT_ID, "capital": round(capital, 2),
        "pool": pool, "ledger": ledger, "note": note,
        "hold_days": G.HOLD_DAYS, "hold_dates": hold_dates,
        "sells": [], "buys": [], "skips": [],
    }
    orders = []
    seq0 = 0
    try:
        seq0 = int(read_heart(date).get("last_cmd_seq_processed", 0) or 0)
    except Exception:
        pass

    sold_codes = set()

    # ---- 卖出：持仓满 HOLD_DAYS 到期 → SELL（止损/止盈由桥内风控处理） ----
    for code, ld in ledger.items():
        since = hold_dates.get(code)
        if since:
            held_days, cal_ok = _count_trade_days(since, date)
            if held_days < G.HOLD_DAYS:
                plan["skips"].append({"code": code, "vol": ld["vol"], "reason": "持有未满%d日(%s起,已%d日,日历%s)" % (
                    G.HOLD_DAYS, since, held_days, "OK" if cal_ok else "缺失")})
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
            "reason": "g2持有到期(满%d日)" % G.HOLD_DAYS, "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
        })
        plan["sells"].append({"code": code, "vol": vol, "cost": ld["cost"], "price": round(price, 3)})
        sold_codes.add(code)

    # ---- 买入：持仓数 < TOP_N 时从候选池补足（对齐回测 while len(hold) < TOP） ----
    # 保留持仓 = 未到期未卖出的持仓；空位数 = TOP_N - 保留持仓数
    kept = {c for c in ledger if c not in sold_codes}
    n_slots = G.TOP_N - len(kept)
    if n_slots > 0 and pool:
        # 候选池：排除已保留持仓，按 total_new 降序
        cand = [p for p in pool if p["code"] not in kept]
        cand.sort(key=lambda p: p["total"], reverse=True)
        budget_each = capital * (1 - G.RESERVE_CASH_PCT) / float(n_slots)
        for i in range(n_slots):
            if not cand:
                break
            best = cand.pop(0)
            code = best["code"]
            price = fetch_price(code)
            if not price or price <= 0:
                plan["skips"].append({"code": code, "reason": "取价失败跳过"})
                continue
            # ---- 低开校验（G2-V1.0，T3）：极端低开(<=-5%)跳过；低开(-5~-3%)需放量承接才放行 ----
            # 003005 型（低开-10%跌停接刀）→ 跳过；601999 型（低开-4.9%放量翻红）→ 放行
            _ohlc = _fetch_ohlc(code)
            if _ohlc:
                _avg = _recent_avg_vol(code)
                _vr = (_ohlc["vol_lot"] / _avg) if (_avg and _avg > 0) else None
                _skip, _why = PF.gap_guard(code, price, _ohlc["pre_close"], _ohlc["open"], _vr)
                if _skip:
                    plan["skips"].append({"code": code, "reason": _why})
                    print(f"    !! {code} 低开拦截: {_why}")
                    continue
            vol = int(budget_each / price / G.MIN_ORDER_VOL) * G.MIN_ORDER_VOL
            if vol <= 0:
                plan["skips"].append({"code": code, "reason": "资金不足一手(%s@%.2f)" % (code, price)})
                continue
            orders.append({
                "action": "BUY", "code": code, "vol": vol, "price": round(price, 3),
                "reason": "g2选股Top%d(total=%.1f)" % (i + 1, best["total"]),
                "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
            })
            plan["buys"].append({"code": code, "vol": vol, "price": round(price, 3),
                                 "held": 0, "target_vol": vol, "total": best["total"]})
    return orders, plan


def main():
    ap = argparse.ArgumentParser(description="G2 每日换仓（先卖后买，写大QMT 桥）")
    ap.add_argument("--date", default=None)
    ap.add_argument("--top", type=int, default=G.SELECT_TOP, help="候选池大小（默认10，对齐回测 TOP10）")
    ap.add_argument("--capital", type=float, default=None, help="资金池覆盖（默认读 g2_strategy_capital.json）")
    ap.add_argument("--live", action="store_true", help="真写桥 cmd/orders_<date>.json（缺省 dry-run）")
    args = ap.parse_args()

    date = args.date or today_str()
    G.SELECT_TOP = args.top
    capital = args.capital or G.load_g2_capital()

    alive, msg = is_bridge_alive(max_age=600)  # date=None→读最新心跳，避免数据日期旧心跳误判桥死
    print("== G2 换仓（%s）==" % date)
    print("桥存活: %s | %s" % (alive, msg))
    print("资金池: %.0f 元（%s）" % (capital, G.G2_CAPITAL_FILE))

    orders, plan = build_plan(date, capital)
    print(plan["note"])
    # N=10 持有期跳过项（掉出 top2 但未满持有期）
    for sk in plan.get("skips", []):
        if "持有未满" in sk.get("reason", ""):
            print("  [SKIP] %s %s" % (sk.get("code", "?"), sk.get("reason", "")))
    if not orders:
        print("[NOOP] 无买卖指令")
        plan["orders"] = []
        _save_plan(plan, date)
        _notify_feishu("【G2换仓 %s】今日无买卖（持仓未满 %d 日，保持 %d 只）" % (date, G.HOLD_DAYS, len(plan.get("ledger", {}))))
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
        _notify_feishu("【G2换仓 %s】已写桥 seq=%d，共 %d 条\n%s" % (
            date, seq, len(orders),
            "\n".join("  %s %s %d股@%.3f" % (o["action"], o["code"], o["vol"], o["price"]) for o in orders)))
        # 更新持仓建仓日并落盘（N=10 持有期记账）
        hd = _update_hold_dates(plan, date)
        save_hold_dates(hd)
        print("持仓建仓日已更新: %s" % json.dumps(hd, ensure_ascii=False))
        plan["hold_dates_after"] = hd
        # 生成当日 positions_cfg 成本锚（桥内止损/对账账本依赖；T-20260902-005 自动化）
        try:
            rc = _gen_positions_cfg(date)
            print("[positions_cfg] 生成结果 exit=%d" % rc)
        except Exception as e:
            print("[positions_cfg] 生成失败（不影响换仓写单）: %s" % e)
    else:
        print("DRY-RUN：未写桥。确认后加 --live。")
        plan["written"] = False
        # dry-run 预览建仓日变化（不落盘）
        hd_prev = _update_hold_dates(plan, date)
        print("（dry-run 建仓日预览）: %s" % json.dumps(hd_prev, ensure_ascii=False))
        plan["hold_dates_preview"] = hd_prev
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
