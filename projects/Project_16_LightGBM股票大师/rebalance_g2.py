# coding: utf-8
"""G2（大QMT 文件桥）每日换仓执行 —— 与 V1.3（miniQMT 67014907）**完全隔离**。

流程（09:45 开盘 15 分钟后执行，先卖后买）：
  1) 读 g2 选股 data/selections/g2/<date>_g2_top10.csv（deploy_predict_g2 --top 10 产出，total>=60 已过滤）
  2) 读 G2 账本持仓（只认 G2 自己的：cmd/positions_cfg_<date>.json 成本锚 + fills FIFO 推导；
     绝不读/纳管账户全量持仓里他人的票）
  3) 卖出：G2 账本中不在目标 topN 的 → SELL（可卖量受 can_use 限制，T+1 锁定不卖）
  4) 买入：目标中持有不足的 → BUY 补到目标股数（等权对齐资金池 98%/N，整手）
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
import hashlib
import io
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
    fetch_price, notify_feishu,
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
    - 账本中已不存在的 code（桥内风控卖出/手工纠正卖出不进 plan.sells）→ 清理陈旧条目（DE 体检 P1-3）
    返回更新后的 hold_dates dict（仅 live 时落盘）。"""
    hd = load_hold_dates()
    # 卖出清仓移除；但「等权减仓」非清仓（超配减仓后仍持有），必须保留建仓日——
    # 否则 positions_cfg 丢票 → 桥内止损不再纳管（T-20260922-005 实盘止损失控，对齐 ENS 版）
    for s in plan.get("sells", []):
        if s.get("note") == "等权减仓":
            continue
        hd.pop(s.get("code", ""), None)
    # 买入（可能补仓也视为重新建仓；对已有持仓的加仓不覆盖原建仓日，保持最早建仓日语义）
    for b in plan.get("buys", []):
        code = b.get("code", "")
        if code and code not in hd:
            hd[code] = date
    # 清理陈旧条目：当前账本（plan.ledger）已不存在的 code（桥内 STOP/TP/TRAIL 出场、correction 卖出）；
    # 但本次新买入 code（buys 里、成交后才进账本）必须保留，否则建仓日被误删 → 持有期计数失效
    # （2026-09-14 实证：603332.SH 当日 BUY 后建仓日丢失，T-20260914-004）
    # T-20260922-005：等权减仓 code 也加入 active，防止被陈旧清理误删（减仓非清仓）
    active = set((plan.get("ledger") or {}).keys())
    active |= {b.get("code", "") for b in plan.get("buys", []) if b.get("code")}
    active |= {s.get("code", "") for s in plan.get("sells", []) if s.get("note") == "等权减仓"}
    for code in list(hd.keys()):
        if code not in active:
            hd.pop(code, None)
    # 保留活跃持仓中既有建仓日（load_g2_ledger 的持仓未动者自然保留）
    return hd


def _fetch_ohlc(code):
    """腾讯行情取 现价/昨收/今开/当日成交量(手)/实时名称。失败返回 None（G2-V1.0 低开校验用）。
    name（T-20261012-001）：实时证券简称（含 ST 前缀），供 PF.st_guard 免二次请求直判。"""
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
                        "open": float(f[5]), "vol_lot": float(f[6]),
                        "name": f[1].strip() if len(f) > 1 and f[1].strip() else None}
    except Exception:
        pass
    return None


def load_hs300_pct():
    """取沪深300 当日涨跌幅（%）。腾讯行情实时取 现价/昨收 计算；失败返回 None。

    环境变量 G2_HS300_PCT 可显式注入（测试/演练用）。"""
    try:
        inj = os.environ.get("G2_HS300_PCT", "").strip()
        if inj:
            return float(inj)
    except Exception:
        pass
    try:
        import urllib.request
        url = "http://qt.gtimg.cn/q=%s" % G.HS300_QT_SYMBOL
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=5).read().decode("gbk", errors="ignore")
        if "~" in raw:
            f = raw.split("~")
            if len(f) > 4:
                price = float(f[3])
                pre = float(f[4])
                if pre > 0:
                    return round((price / pre - 1.0) * 100.0, 4)
    except Exception:
        pass
    return None


def _calc_tier(hs300_pct):
    """沪深300 当日涨跌幅(%) → T 档（T-20260904-004）：
    T=0 停买 / T=1 半仓 / T=2 满仓；None（数据缺失）→ None（调用方 fail-safe 半仓）。
    边界对齐 V1.3：停买 ≤ -1.5%；半仓 **<** -1.0%（恰 -1.0% 判满仓，DE 体检 P2-6 统一）。"""
    if hs300_pct is None:
        return None
    if hs300_pct <= G.TIER_STOP_PCT:
        return 0
    if hs300_pct < G.TIER_HALF_PCT:
        return 1
    return 2


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


def _candidate_meta(date, csv_path, loader):
    """候选溯源 meta（2026-09-28 候选可审计性修复①，缺陷B）。

    背景：g2 候选命名空间有多个写手（夜跑正品/次日副本/TRAE 09:25 重算/复跑对比），
    事后文件可能被覆写（r1 误判即由此产生）。本 meta 让每次换仓记录都能回答
    「当时到底读了哪个文件、内容是什么、是否被事后改动」。
    loader: exact=运行日精确匹配 / fallback=回退最新数据日 / none=无可用候选。
    """
    meta = {
        "candidate_file": csv_path,        # 实际加载的候选文件路径（None=无候选）
        "candidate_loader": loader,
        "candidate_data_date": None,       # 候选文件名日期（数据日口径）
        "candidate_rows_raw": 0,           # CSV 原始行数（红线过滤前）
        "candidate_sha256": None,          # 内容哈希（核心：防事后覆写失真）
        "candidate_asof": None,            # asof 只读留档路径（读取点字节快照）
    }
    if csv_path:
        b = os.path.basename(csv_path)
        if b[:8].isdigit():
            meta["candidate_data_date"] = b[:8]
    return meta


def _archive_candidate_asof(date, meta, raw):
    """asof 只读留档（修复③之 3d 读取侧变体）：把本次实际读取的候选字节写入
    data/rebalance_g2/candidates_asof/<date>_<sha8>_<原名>。文件名含内容哈希 → 天然幂等，
    内容不同则共存，绝不覆盖已有留档。失败只打日志，绝不阻断换仓。"""
    try:
        adir = os.path.join(G.DATA_DIR, "rebalance_g2", "candidates_asof")
        os.makedirs(adir, exist_ok=True)
        sha8 = (meta.get("candidate_sha256") or "nasha")[:8]
        base = os.path.basename(meta.get("candidate_file") or "candidate.csv")
        adst = os.path.join(adir, "%s_%s_%s" % (date, sha8, base))
        if not os.path.exists(adst):
            with open(adst, "wb") as f:
                f.write(raw)
        meta["candidate_asof"] = adst
    except Exception as e:
        print("  [asof留档] 失败（不阻断换仓）: %s" % e)


def load_g2_selection(date, top_n):
    """读 g2 选股 CSV → 候选池（total>=REDLINE，按 total 降序；不截断，买入侧自行排除持仓取最高）。

    先按运行日 <date> 精确匹配；缺失则回退最新数据日候选（见 _latest_avail_candidate）。
    绝不回退 V1.3 的 selection_full.csv（G2/V1.3 隔离红线）。
    返回 (picks, note, meta)：
    - meta 为候选溯源（2026-09-28 缺陷B修复①）：单次读盘 + 读取点即时 SHA256 + asof 留档，
      保证 sha/内容/留档三者同源一致（R2），事后候选被覆写也能复现实盘输入。
    """
    csv_path = os.path.join(G.G2_SELECT_DIR, "%s_g2_top%d.csv" % (date, top_n))
    note = "g2 候选池 %d 只（红线 %.0f）" % (top_n, G.REDLINE)
    loader = "exact"
    if not os.path.exists(csv_path):
        csv_path, note = _latest_avail_candidate(date, top_n)
        loader = "fallback"
        if csv_path is None:
            return [], note, _candidate_meta(date, None, "none")
    meta = _candidate_meta(date, csv_path, loader)
    picks = []
    try:
        with open(csv_path, "rb") as f:
            raw = f.read()
        meta["candidate_sha256"] = hashlib.sha256(raw).hexdigest()
        _archive_candidate_asof(date, meta, raw)
        text = raw.decode("utf-8-sig")
    except Exception as e:
        meta["candidate_loader"] = "read_error"
        return [], "g2 候选读取失败(%s)" % e, meta
    for row in csv.DictReader(io.StringIO(text)):
        meta["candidate_rows_raw"] += 1
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
        return [], "g2 选股无 total>=%.0f 的票（宁缺毋滥）" % G.REDLINE, meta
    picks.sort(key=lambda p: p["total"], reverse=True)
    return picks, "g2 候选池 %d 只（红线 %.0f）" % (len(picks), G.REDLINE), meta


def load_g2_ledger(date):
    """G2 账本持仓（2026-10-22 起改为**独立成交账本**推导）。

    旧口径：positions_cfg（内置桥成本锚）+ state/fills FIFO —— 随文件桥作废（沙箱下内置侧读不到）。
    新口径：g2_trade_log.csv（外部 xtquant 下单后自写）BUY-SELL 净额 + FIFO 成本。
    账本 account_id 戳不符 → 自动备份 + 空仓起步（AGENTS T-20260823-004）。
    绝不读账户全量持仓（同账号三策略并存，账户持仓含他人票）。

    返回 {code: {"vol": int, "cost": float, "src": str}}。
    """
    import xt_exec
    try:
        led = xt_exec.ledger_from_trades(G)
    except Exception as e:
        print("  !! [ledger] 账本推导失败（按空仓处理，宁可不交易）: %r" % (e,))
        return {}
    return {c: {"vol": int(v["vol"]), "cost": float(v["cost"]), "src": "trade_log"}
            for c, v in led.items() if v["vol"] > 0}


def load_sellable(date, code):
    """查可卖量（T+1 锁定=0）。连接账户实时查询；失败返回 None（调用方按全量 vol 处理）。

    仅取 can_use，不据此判定归属（归属只认本策略账本）。
    """
    try:
        import xt_exec
        trader, account = xt_exec.connect(G)
        try:
            pos = xt_exec.query_positions(trader, account)
        finally:
            try:
                trader.stop()
            except Exception:
                pass
        return int((pos.get(code) or {}).get("can_use", 0))
    except Exception as e:
        print("  !! [sellable] 查可卖量失败（按账本全量处理）: %r" % (e,))
        return None



def build_plan(date, capital):
    """生成换仓计划（卖 + 买）。对齐回测 simulate 语义（scan_rotate_cost_real）：
    - 卖出：持仓满 HOLD_DAYS 到期 → SELL（不看是否在候选池内；止损/止盈由桥内风控处理）
    - 买入：持仓数 < TOP_N 时，从候选池（Top10）选 total_new 最高、不在持仓、过红线的补足（while len(hold) < TOP）
    返回 (orders, plan_dict)。"""
    pool, note, cand_meta = load_g2_selection(date, G.SELECT_TOP)   # 候选池（Top10）+ 溯源 meta
    ledger = load_g2_ledger(date)
    hold_dates = load_hold_dates()   # {code: "YYYYMMDD"} 持仓建仓日
    plan = {
        "date": date, "account_id": G.ACCOUNT_ID, "capital": round(capital, 2),
        "pool": pool, "ledger": ledger, "note": note,
        # ---- 候选溯源（2026-09-28 缺陷B修复①：事后候选被覆写也能复现实盘输入）----
        "candidate_file": cand_meta["candidate_file"],
        "candidate_loader": cand_meta["candidate_loader"],
        "candidate_data_date": cand_meta["candidate_data_date"],
        "candidate_rows": len(pool),                # 红线过滤后候选数
        "candidate_rows_raw": cand_meta["candidate_rows_raw"],
        "candidate_sha256": cand_meta["candidate_sha256"],
        "candidate_asof": cand_meta["candidate_asof"],
        "hold_days": G.HOLD_DAYS, "hold_dates": hold_dates,
        "sells": [], "buys": [], "skips": [],
    }
    orders = []
    # 2026-10-22：seq0 原取自桥心跳 last_cmd_seq_processed，文件桥已作废 → 固定 0
    #（strategy_order_id 仅作人读标识，不再参与桥侧幂等；幂等改由账本 + 互斥锁承担）
    seq0 = 0

    sold_codes = set()

    # ---- 卖出：持仓满 HOLD_DAYS 到期 → SELL（止损/止盈由桥内风控处理） ----
    for code, ld in ledger.items():
        # ---- ST 风险警示强制剔除（T-20261012-001，诚哥拍板 2026-10-12）----
        # ST 持仓不等持有期，下个换仓日强制卖出（背景：10-12 实盘买入 *ST华鹏 603021，
        # 特征快照无 is_st 列致候选链盲区）。T+1 锁定则顺延次日换仓再卖（st_guard 每日重判）。
        _st_skip, _st_why = PF.st_guard(code)
        if _st_skip:
            sellable = load_sellable(date, code)
            vol = ld["vol"]
            if sellable is not None:
                vol = min(vol, sellable)
            if vol <= 0:
                plan["skips"].append({"code": code, "vol": ld["vol"], "reason": "ST剔除但T+1锁定可卖0(次日换仓再卖)"})
                print("    !! %s %s 但 T+1 锁定可卖 0，次日换仓再卖" % (code, _st_why))
                continue
            price = fetch_price(code) or ld["cost"]
            orders.append({
                "action": "SELL", "code": code, "vol": vol, "price": round(price, 3),
                "reason": "%s(强制卖出)" % _st_why, "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
            })
            plan["sells"].append({"code": code, "vol": vol, "cost": ld["cost"], "price": round(price, 3)})
            sold_codes.add(code)
            print("    !! [ST剔除] %s 强制卖出 %d 股（不等持有期）" % (code, vol))
            continue
        since = hold_dates.get(code)
        if not since:
            # T-20260904-006①：建仓日缺失 fail-open 立即卖出 → 改 fail-safe 跳过+告警（防误清仓）
            plan["skips"].append({"code": code, "vol": ld["vol"],
                                  "reason": "建仓日缺失，fail-safe 跳过卖出（防误清仓）"})
            print("    !! %s hold_date 缺失，跳过卖出（fail-safe，需人工核查账本）" % code)
            continue
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
    # ---- 纸面自动降级闸（2026-09-13 立，T-20260913-001 P1-8）：冻结时只卖不买 ----
    frozen = False
    try:
        import paper_forward_downgrade as _pf
        frozen = _pf.is_frozen("G2")
    except Exception:
        pass
    if frozen:
        n_slots = 0
        plan["downgrade_frozen"] = True
        print("    !! [降级闸] G2 纸面前向样本外为负，冻结加仓（只卖不买）——已持仓到期卖出照常，桥内风控照常")
    # ---- 大盘门控（T-20260904-004，2026-09-11 补上）：T=0 停买 / T=1 半仓 / T=2 满仓 ----
    hs300_pct = load_hs300_pct()
    tier = _calc_tier(hs300_pct)
    if tier is None:
        tier = 1  # 数据缺失 fail-safe 半仓（刹车数据缺失时降速不裸奔）
        print("    !! [门控] 沪深300 涨跌幅获取失败，fail-safe 按 T=1 半仓执行")
    deploy_pct = G.HALF_DEPLOY_PCT if tier == 1 else (0.0 if tier == 0 else G.DEPLOY_PCT)
    plan["hs300_pct"] = hs300_pct
    plan["tier"] = tier
    plan["deploy_pct"] = deploy_pct
    hs_disp = "N/A" if hs300_pct is None else ("%.2f%%" % hs300_pct)
    if tier == 0:
        print("    [门控] 沪深300 %s <= 停买线 %.1f%% → T=0 停买（只卖不买，空位不补）" % (hs_disp, G.TIER_STOP_PCT))
    elif tier == 1:
        if hs300_pct is None:
            print("    [门控] 沪深300 取数失败 fail-safe → T=1 半仓（买入预算=资金池×%.0f%%）" % (G.HALF_DEPLOY_PCT * 100))
        else:
            print("    [门控] 沪深300 %s <= 半仓线 %.1f%% → T=1 半仓（买入预算=资金池×%.0f%%）" % (hs_disp, G.TIER_HALF_PCT, G.HALF_DEPLOY_PCT * 100))
    else:
        print("    [门控] 沪深300 %s → T=2 满仓（买入预算=资金池×%.0f%%）" % (hs_disp, G.DEPLOY_PCT * 100))
    if n_slots > 0 and pool and deploy_pct > 0:
        # 候选池：排除已保留持仓 + ENS 账本重叠票（反向互斥对称化，2026-09-13，T-20260913-001 C2）
        # 背景：融合换仓已单向跳过 G2 账本（g2_hold_dates.json）；G2 侧此前无反向互斥，
        #       G2 候选若撞 ENS 持仓会双买（T-20260910-109 已知残余风险）。同账户双桥，杜绝争同一持仓。
        ens_held = set()
        try:
            if os.path.exists(G.ENS_HOLD_DATES_FILE):
                with open(G.ENS_HOLD_DATES_FILE, encoding="utf-8") as f:
                    ens_held = set(str(k) for k in (json.load(f).get("hold_dates", {}) or {}).keys())
            else:
                print("  !! [重叠规避] ENS 账本文件缺失（%s）——无法互斥，双买风险回归，需核查" % G.ENS_HOLD_DATES_FILE)
        except Exception as e:
            # P2-5 修复：读取失败必须告警（fail-open 只在不排除时容忍，但不可静默）
            print("  !! [重叠规避] ENS 账本读取失败（%s）——无法互斥，双买风险回归，需核查" % e)
        cand = [p for p in pool if p["code"] not in kept and p["code"] not in ens_held]
        if ens_held:
            print("  [重叠规避] 跳过 ENS 账本持仓 %d 只: %s" % (len(ens_held), ",".join(sorted(ens_held))))
        cand.sort(key=lambda p: p["total"], reverse=True)
        # ---- 动态等权（2026-09-22 对齐 V1.3，修复 TOP_N 上调后补仓资金不足）----
        # 原实现"只补空位 + budget=(资金池−kept成本)/空位"：TOP_N=2→3 后若老仓已占满资金池，
        # avail≈0 → 补第 3 只手不足一手被 skip，扩不到新 TOP_N。改为 V1.3 式等权：
        #   target_value = 资金池 × deploy_pct / TOP_N（固定分母，市值口径）
        #   对最终持有集每只：超配(held×现价>target_value)→SELL 减仓到目标股数释放资金；
        #   新晋票→建仓到目标股数。一次性扩到 TOP_N 且基本等权。
        # 保守口径：kept（未到期保留持仓，可能已掉出候选）只做下界减仓、不下逆信号加仓；
        # 并行于到期卖出段之后，超配减仓 SELL 全部排在 BUY 前（先卖后买）。
        # 注：此实盘等权口径与回测 scan_rotate_cost_real「只补空位」不同，历史回测数字须标注口径前提。
        #
        # 第一步：从候选排除持仓取最高分，预取最多 n_slots 只新晋票（取价+低开校验通过）
        new_targets = []   # [(code, price, total)]
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
            _ohlc = _fetch_ohlc(code)
            # ---- ST 风险警示买入拦截（T-20261012-001）：主库快照 + 腾讯实时名称双层 ----
            # name 直用 _fetch_ohlc 已取的实时简称（含 ST 前缀），免二次请求；两层皆失 fail-open。
            _st_skip, _st_why = PF.st_guard(code, name=(_ohlc or {}).get("name"))
            if _st_skip:
                plan["skips"].append({"code": code, "reason": _st_why})
                print(f"    !! {code} ST 拦截: {_st_why}")
                continue
            if _ohlc:
                _vr = PF.gap_volume_ratio(code, _ohlc["vol_lot"])
                _skip, _why = PF.gap_guard(code, price, _ohlc["pre_close"], _ohlc["open"], _vr)
                if _skip:
                    plan["skips"].append({"code": code, "reason": _why})
                    print(f"    !! {code} 低开拦截: {_why}")
                    continue
            new_targets.append((code, price, best["total"]))

        # 第二步：等权目标市值（固定分母 TOP_N）
        target_value = capital * deploy_pct / float(G.TOP_N)
        plan["target_value"] = round(target_value, 2)

        # 第三步：对最终持有集平衡 —— 超配减仓收集（全部 SELL 先于 BUY）
        rebalance_sells = []   # 超配减仓（对 kept，未到期下界修正，释放资金）
        balance_buys = []      # 新晋建仓（held=0 -> target_value）
        # 3.1 新晋票建仓（总量 = target_value → target_vol 整手）
        for code, price, total in new_targets:
            target_vol = int(target_value / price / G.MIN_ORDER_VOL) * G.MIN_ORDER_VOL
            if target_vol < G.MIN_ORDER_VOL:
                plan["skips"].append({"code": code, "reason": "资金不足一手(%s@%.2f)" % (code, price)})
                continue
            balance_buys.append({"code": code, "vol": target_vol, "price": round(price, 3), "total": total})
        # 3.2 kept 超配减仓（仅当 held_value > target_value 且可卖充足）
        for code in kept:
            ld = ledger.get(code, {})
            held_vol = int(ld.get("vol", 0) or 0)
            price = fetch_price(code) or ld.get("cost", 0)
            if not price or price <= 0:
                continue
            target_vol = int(target_value / price / G.MIN_ORDER_VOL) * G.MIN_ORDER_VOL
            diff = held_vol - target_vol
            if diff <= 0:
                continue  # 未超配或资金不足一手，不动（不下逆信号加仓）
            sellable = load_sellable(date, code)
            v = min(diff, sellable) if sellable is not None else diff
            if v < G.MIN_ORDER_VOL:
                plan["skips"].append({"code": code, "vol": held_vol, "reason": "超配但T+1锁定/不足一手"})
                continue
            rebalance_sells.append({"code": code, "vol": v, "cost": ld.get("cost", 0),
                                    "price": round(price, 3)})
        # 第四步：先卖后买 —— 超配减仓 SELL 排在所有 BUY 之前（承接到期 SELL 段之后）
        for s in rebalance_sells:
            orders.append({
                "action": "SELL", "code": s["code"], "vol": s["vol"], "price": s["price"],
                "reason": "动态等权超配减仓(目标市值%.0f)" % target_value,
                "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
            })
            plan["sells"].append({"code": s["code"], "vol": s["vol"], "cost": s["cost"],
                                  "price": s["price"], "note": "等权减仓"})
        for j, b in enumerate(balance_buys):
            orders.append({
                "action": "BUY", "code": b["code"], "vol": b["vol"], "price": b["price"],
                "reason": "g2选股Top%d(total=%.1f)" % (j + 1, b["total"]),
                "strategy_order_id": "P16_%s_%04d" % (date, seq0 + len(orders) + 1),
            })
            plan["buys"].append({"code": b["code"], "vol": b["vol"], "price": b["price"],
                                 "held": 0, "target_vol": b["vol"], "total": b["total"]})
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

    # 2026-10-22：桥心跳检查删除——文件桥已作废，改为 xtquant 直连（连接失败在 live 分支 abort）
    print("== G2 换仓（%s）==" % date)
    print("资金池: %.0f 元（%s）" % (capital, G.G2_CAPITAL_FILE))

    orders, plan = build_plan(date, capital)
    plan["written"] = bool(args.live)   # dry-run 写旁路文件，防覆盖当日 live 执行记录（DE 体检 P2-9）
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
        # 2026-10-22 拍板：改为 xtquant 直连真实下单（原「写桥 cmd/orders_*.json」通道作废，
        # QMT 沙箱下内置侧读不到）。桥心跳检查随之删除——不再依赖文件桥。
        import xt_exec
        print("[LIVE] 连接 miniQMT %s ..." % G.ACCOUNT_ID)
        try:
            trader, account = xt_exec.connect(G)
        except Exception as e:
            print("[ABORT] 连接失败，未下任何单: %r" % (e,))
            return 1
        try:
            result = xt_exec.execute_orders(G, trader, account, orders, date)
        finally:
            try:
                trader.stop()
            except Exception:
                pass
        print("执行汇总: %s" % json.dumps(result["summary"], ensure_ascii=False))
        if result["failures"]:
            print("!! 失败 %d 笔: %s" % (
                len(result["failures"]),
                json.dumps(result["failures"], ensure_ascii=False)[:800]))
        plan["written"] = True
        plan["execution"] = result["summary"]
        plan["execution_records"] = result["records"]
        plan["execution_failures"] = result["failures"]
        _notify_feishu("【G2换仓 %s】已下单 %d 笔（成交 %d 股，失败 %d 笔）\n%s" % (
            date, result["summary"]["n"], result["summary"]["traded"],
            result["summary"]["failures"],
            "\n".join("  %s %s %d股@%.3f" % (x["side"], x["code"], x["traded_vol"], x["fill_price"])
                      for x in result["records"])))
        # 更新持仓建仓日并落盘（N=15 持有期记账）
        hd = _update_hold_dates(plan, date)
        save_hold_dates(hd)
        print("持仓建仓日已更新: %s" % json.dumps(hd, ensure_ascii=False))
        plan["hold_dates_after"] = hd
        # 注：positions_cfg 成本锚随文件桥作废，不再生成（内置风控改为硬编码白名单，见方案 P0-5）
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
    suffix = "" if plan.get("written") else ".dryrun"
    jp = os.path.join(out_dir, "rebalance_g2_%s%s.json" % (date, suffix))
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    mp = os.path.join(out_dir, "rebalance_g2_%s%s.md" % (date, suffix))
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# G2 换仓计划 %s%s\n\n" % (date, "（DRY-RUN 旁路）" if suffix else ""))
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
