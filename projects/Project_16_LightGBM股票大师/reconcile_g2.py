# coding: utf-8
"""G2（大QMT 文件桥）日终对账 —— 与 V1.3（reconcile_trades.py）**完全隔离**。

核对：
  1) fills 每笔状态（FILLED/CANCELED/REJECTED/UNCONFIRMED…）
  2) 桥心跳 pending 是否残留（活跃单未收尾）
  3) G2 账本（positions_cfg/fills 推导）vs 账户持仓 → 识别孤儿仓（账户有票但 G2 账本无 =
     调试遗留/他人策略，G2 不纳管不误杀，仅报告）
  4) 资产快照

用法：
  python reconcile_g2.py                # 今天
  python reconcile_g2.py --date 20260902
输出：data/reconcile_g2/reconcile_g2_<date>.md/.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g2_config as G
import qmt_bridge_client as QC
from qmt_bridge_client import read_fills, read_positions, read_asset, read_heart, positions_from_fills, notify_feishu, fetch_price

TERMINAL_STATUS = ("FILLED", "PARTIAL_FILLED", "CANCELED", "REJECTED", "UNCONFIRMED", "ABANDONED")


def _state_dir():
    """state 目录的**唯一**口径（2026-10-31 CC 复核 §5）。

    为什么需要它：`g2_config.STATE_DIR` 与 `qmt_bridge_client.STATE_DIR` 是两个独立模块常量，
    而真正读文件的 `read_fills`/`read_positions` 用的是**后者**。本文件早期混用两者
    （`_all_fills` 列举用 G、`_roll_g2_capital` 与 `main()` 查文件用 G），造成「列 A 目录、
    读 B 目录」的隐患——生产上两者恰好同路径，但靠巧合对齐。
    现统一取 `QC.STATE_DIR`（读侧口径），并在模块加载时校验两者一致，不等就大声报出来。
    """
    return getattr(QC, "STATE_DIR", G.STATE_DIR)


if _state_dir() != getattr(G, "STATE_DIR", None):
    print("[reconcile_g2] WARN STATE_DIR 口径不一致：g2_config=%r / qmt_bridge_client=%r"
          "（本脚本一律按后者，请核对配置）" % (getattr(G, "STATE_DIR", None), _state_dir()))


def _all_fills():
    """遍历 state 目录所有 fills_<date>.json，按日期序返回全部成交（跨日累计已实现盈亏用）。

    ★ 2026-10-30 CC 复核 §4.8：这里**列举**用 `G.STATE_DIR`，而真正读文件的是
    `qmt_bridge_client.read_fills()`，它用的是**另一个模块的** `qmt_bridge_client.STATE_DIR`。
    两个常量今天恰好指向同一路径（`D:/QMT_POOL/g2_bridge/state`），但它们是靠巧合对齐的——
    列 A 目录、读 B 目录，谁改了一边就会出现「列得到、读不到」的静默漏算
    （测试里 monkeypatch `G.STATE_DIR` 也到不了 `read_fills`，导致 K3 变成空断言）。
    现统一为 `qmt_bridge_client.STATE_DIR`（读侧口径），保证「列」和「读」同源。
    """
    out = []
    try:
        base = _state_dir()
        files = [f for f in os.listdir(base) if f.startswith("fills_") and f.endswith(".json")]
        files.sort()
        for fn in files:
            d = read_fills(fn[len("fills_"):-5])
            if d and d.get("fills"):
                out.extend(d["fills"])
    except Exception:
        pass
    return out


def _other_strategy_hold_codes():
    """读另一策略(融合ENS)持仓 code 集合（同券商账号 70180771，不同桥目录）——
    对账时用于隔离：对方策略的持仓不算 G2 孤儿仓（AGENTS：两策略共账号，各自账本仅认自己）。
    """
    other_dir = "D:/QMT_POOL/p16_ensemble_bridge/cmd"
    codes = set()
    try:
        files = sorted([f for f in os.listdir(other_dir)
                        if f.startswith("positions_cfg_") and f.endswith(".json")])
        # 取最新一份（YYYYMMDD 字典序 = 日期序）
        for fn in reversed(files):
            try:
                with open(os.path.join(other_dir, fn), encoding="utf-8") as f:
                    d = json.load(f)
                if str(d.get("account_id", "")) == G.ACCOUNT_ID:
                    for p in d.get("positions", []):
                        if p.get("code"):
                            codes.add(p["code"])
                    break
            except Exception:
                continue
    except Exception:
        pass
    return codes


def _realized_pnl_from_fills():
    """跨全部历史 fills FIFO 配对算累计已实现盈亏（不含费用，与 positions_from_fills 同口径）。"""
    from collections import deque
    buys = {}
    realized = 0.0
    for f in _all_fills():
        status = str(f.get("status", ""))
        if status not in ("FILLED", "PARTIAL_FILLED"):
            continue
        code = str(f.get("code", "") or "")
        action = str(f.get("action", "") or "").upper()
        if not code or action not in ("BUY", "SELL"):
            continue
        try:
            vol = int(float(f.get("vol", 0) or 0))
            price = float(f.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if vol <= 0 or price <= 0:
            continue
        if action == "BUY":
            buys.setdefault(code, deque()).append((vol, price))
        else:
            sv = vol
            while sv > 0 and buys.get(code):
                v, cp = buys[code][0]
                take = min(v, sv)
                sv -= take
                realized += (price - cp) * take
                buys[code][0] = (v - take, cp)
                if buys[code][0][0] <= 0:
                    buys[code].popleft()
    return realized


def _float_pnl_now():
    """当前 G2 持仓浮盈 = Σ(现价-成本)×vol。持仓用最新 positions_cfg（成本含费，account 戳校验）。"""
    try:
        files = [f for f in os.listdir(G.CMD_DIR) if f.startswith("positions_cfg_") and f.endswith(".json")]
        files.sort(reverse=True)
    except Exception:
        files = []
    pos = []
    for fn in files:
        try:
            with open(os.path.join(G.CMD_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            if str(d.get("account_id", "")) == G.ACCOUNT_ID:
                pos = d.get("positions", []) or []
                break
        except Exception:
            continue
    pnl = 0.0
    for p in pos:
        code = p.get("code", "")
        vol = int(p.get("vol", 0) or 0)
        cost = float(p.get("cost", 0) or 0)
        if not code or vol <= 0 or cost <= 0:
            continue
        price = fetch_price(code)
        if price and price > 0:
            pnl += (price - cost) * vol
    return pnl


def _roll_g2_capital(date):
    """资金池滚动：capital = 初始 + 累计已实现盈亏 + 当前持仓浮盈。

    ★ 2026-10-30 数据源门（审计 §3.4，P0，资金分配口径）：
      本函数的两个输入都来自**已作废的文件桥**——
        · `_realized_pnl_from_fills()` 遍历 `state/fills_*.json`（最新 20261013，13 天前）
        · `_float_pnl_now()` 读 `cmd/positions_cfg_*.json`（最新 20261021，9 天前）
      而 QMT 内置 `open()` 恒抛 `Foribdden FileIO`（文件桥架构性失效），这两个目录
      **永远不会再有新文件**。于是算出的 capital 会永久停在旧值、且今日买入的浮盈
      永远不计入（实测 2026-10-30 写入 93501.0 = 100000 + realized(-6499) + float(0)，
      当日买入 900 股完全没进 float）。这个数又是次日建仓预算的唯一依据。

      故加门：**当日 state 快照不存在就跳过滚动**，宁可沿用上一次的值（并在日志里说清），
      也不用死数据算出一个看起来合理、实际永久偏离的数。

      ⚠ 这是**资金分配行为**的改动。它只做「不写」，不做「算得更好」——
      真正的修复（成交/持仓源从文件桥迁到 `g2_trade_log.csv` 或回环 hub）是架构改动，
      需单独评估，不在本条范围内。
    """
    try:
        # 数据源门：当日快照必须存在
        pos_file = os.path.join(_state_dir(), "positions_%s.json" % date)
        fill_file = os.path.join(_state_dir(), "fills_%s.json" % date)
        miss = [os.path.basename(f) for f in (pos_file, fill_file) if not os.path.exists(f)]
        if miss:
            print("[资金池] 数据源缺当日快照（%s 不存在），本次**不滚动**"
                  "——文件桥已作废，继续算只会永久偏离真实权益" % ",".join(miss))
            return None

        realized = _realized_pnl_from_fills()
        float_pnl = _float_pnl_now()
        capital = G.START_CAPITAL + realized + float_pnl
        if capital <= 0:
            print("[资金池] 计算异常(<=0)，跳过更新")
            return None
        G.save_g2_capital(round(capital, 2), note="reconcile 滚动 realized=%.0f float=%.0f" % (realized, float_pnl))
        print("[资金池] 滚动更新: 初始%.0f + 已实现%.0f + 浮盈%.0f = %.2f" % (G.START_CAPITAL, realized, float_pnl, capital))
        return capital
    except Exception as e:
        print("[资金池] 滚动异常（不影响对账）: %s" % e)
        return None


def main():
    ap = argparse.ArgumentParser(description="G2 日终对账")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    date = args.date or time.strftime("%Y%m%d")

    # 对账前刷新当日 positions_cfg 成本锚（T-20260902-005：rebalance --live 已生成，
    # 但风控卖出/当日变化后需按最新账户持仓校准；失败不阻断对账）
    try:
        import gen_positions_cfg_g2 as GPC
        _rc = GPC.main_with_date(date)
        if _rc != 0:
            print("[WARN] positions_cfg 刷新异常 exit=%d，对账用既有账本" % _rc)
    except Exception as e:
        print("[WARN] positions_cfg 刷新失败（不影响对账）: %s" % e)

    report = {
        "date": date, "account_id": G.ACCOUNT_ID,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fills": [], "pending": [], "orphans": [], "cross_strategy": [], "ledger": {}, "asset": {},
        "issues": [],
    }

    # ① fills
    fills = read_fills(date) or {}
    fill_list = fills.get("fills", [])
    report["fills_total"] = len(fill_list)
    report["fills"] = [{"strategy_order_id": f.get("strategy_order_id", ""),
                        "code": f.get("code", ""), "action": f.get("action", ""),
                        "vol": f.get("vol", 0), "price": f.get("price", 0),
                        "status": f.get("status", ""), "reason": f.get("reason", "")}
                       for f in fill_list]
    by_status = {}
    for f in fill_list:
        by_status[str(f.get("status", ""))] = by_status.get(str(f.get("status", "")), 0) + 1
    report["fills_by_status"] = by_status
    # 未终态 fills（异常）
    for f in fill_list:
        if str(f.get("status", "")) not in TERMINAL_STATUS:
            report["issues"].append("fills 未终态: %s %s" % (f.get("strategy_order_id"), f.get("status")))

    # ② pending（心跳残留活跃单）
    heart = read_heart(date) or {}
    pending = heart.get("pending", []) or []
    report["pending"] = pending
    report["pending_count"] = heart.get("pending_count", 0)
    if pending:
        report["issues"].append("有 %d 条 pending 未收尾（活跃单风险）" % len(pending))

    # ③ G2 账本 vs 账户持仓 → 孤儿仓
    ledger = {}
    try:
        cfg = json.load(open(os.path.join(G.CMD_DIR, "positions_cfg_%s.json" % date), encoding="utf-8"))
        if str(cfg.get("account_id", "")) == G.ACCOUNT_ID:
            for p in cfg.get("positions", []):
                ledger[p["code"]] = int(p.get("vol", 0) or 0)
    except Exception:
        pass
    try:
        for c, v in (positions_from_fills(date) or {}).items():
            if c not in ledger or ledger[c] <= 0:
                ledger[c] = int(v.get("vol", 0) or 0)
    except Exception:
        pass
    report["ledger"] = ledger
    pos = read_positions(date) or {}
    acct = {p.get("code", ""): int(p.get("volume", 0) or 0) for p in pos.get("positions", [])}
    report["account_positions"] = acct
    # SNAPSHOT_MISSING guard：休市日 QMT 不产出 state/positions_<date>.json → acct 空而账本非空，
    # 差额循环 diff=acct-ledger 全负，会误报"账本比账户多 N 股（卖出未回写/丢失）"
    # （2026-09-25 中秋休市实测：G2 3 条假阳性，人工核对真实差额=0）。
    # 此时跳过差额/孤儿判定，提示写 notes 不进 issues（防 de_guard_runner 以 issues 非空误触发 DE 兜底）。
    #
    # ★ 2026-10-30 修（审计 §3.3，P0）：原条件 `(not acct) and bool(ledger)` 漏掉了「两边都空」——
    #   而 state 目录自文件桥作废（QMT 内置 open() 恒抛 Foribdden FileIO）起就**永远**是两边都空，
    #   于是 snapshot_missing 恒 False、产物恒 `issues: []`，下游一律渲染成「全绿」。
    #   实测 20261022–20261030 连续 7 个交易日 fills=0/ledger=0/acct=0/issues=0，
    #   不是因为没问题，是因为这 7 天对账根本没在看。
    #   现改为「按文件是否在」判定，并用 source_unavailable 显式标注「数据源无当日快照」。
    # 数据源状态：当日 state 文件是否存在（不存在 ≠ 空仓，AGENTS「账号不可读≠空仓」）
    try:
        _pos_file = os.path.join(_state_dir(), "positions_%s.json" % date)
        _fill_file = os.path.join(_state_dir(), "fills_%s.json" % date)
        _has_pos = os.path.exists(_pos_file)
        _has_fill = os.path.exists(_fill_file)
    except Exception:
        _has_pos = _has_fill = False
    report["source"] = {
        "state_positions_today": bool(_has_pos),
        "state_fills_today": bool(_has_fill),
    }
    # source_unavailable = 当日快照文件根本不存在（数据源断了，不是账户空仓）
    report["source_unavailable"] = not _has_pos
    # ★ 2026-10-30 CC 复核 §4.3 / 10-31 §4：`snapshot_missing` 只保留**一个判据**——
    #   「当日快照文件是否在」。早期写法 `not acct` 把「文件缺失」与「账户真空仓」揉在一起，
    #   后来又同时留了 `not acct`（局部）和 `not _has_pos`（发布字段），造成：
    #     · `report["snapshot_missing"]` 先被写成 `not acct` 再被覆盖（死赋值）；
    #     · 分支用的是局部 `not acct`，与发布字段 `not _has_pos` 可能分叉。
    #   现在统一：字段 = 分支 = `not _has_pos`。
    snapshot_missing = not _has_pos
    report["snapshot_missing"] = snapshot_missing
    deltas = {}
    if snapshot_missing:
        report.setdefault("notes", [])
        report["notes"].append(
            "SNAPSHOT_MISSING：账户快照不存在，差额与孤儿判定已跳过，请人工核对上一交易日快照")
        report["notes"].append(
            "SOURCE_UNAVAILABLE：当日 positions_%s.json 不存在（文件桥已作废），"
            "本次对账**未覆盖**成交/持仓——不是「无差额」，是「没在看」" % date)
    else:
        if not acct:
            # 文件在、positions 空 = 真空仓（这是有效结论，不是缺失）
            report.setdefault("notes", [])
            report["notes"].append(
                "EMPTY_POSITIONS：当日快照存在且 positions 为空（账户真空仓），已按空仓对账")
        # 差额对账：超额=账户多（孤儿/重复建仓），不足=账本多（卖出未回写/丢失）
        for code in set(ledger) | set(acct):
            diff = acct.get(code, 0) - ledger.get(code, 0)
            if diff:
                deltas[code] = diff
    report["deltas"] = deltas
    # 跨策略隔离（T-20260922-004）：同账号 70180771 上融合ENS 的持仓对 G2 账本而言是"他人持仓"，
    # 不算 G2 孤儿仓——只提示归属，不进孤儿/告警，避免共享账号互判误报
    other_codes = _other_strategy_hold_codes()
    report["other_strategy_holdings"] = sorted(other_codes)
    for code, diff in deltas.items():
        if diff > 0:
            if code in other_codes:
                report["cross_strategy"].append({"code": code, "vol": diff,
                                                 "hint": "账户比 G2 账本多 %d 股（融合ENS 持仓，同账号非 G2 孤儿）" % diff})
            else:
                report["orphans"].append({"code": code, "vol": diff,
                                          "hint": "账户比 G2 账本多 %d 股（孤儿/其他未知策略，G2 不主动处理，仅预警）" % diff})
                report["issues"].append("持仓差额: %s 账户比账本多 %d 股" % (code, diff))
        else:
            report["issues"].append("持仓差额: %s 账本比账户多 %d 股（卖出未回写/丢失，需排查）" % (code, -diff))
    if report["orphans"]:
        report["issues"].append("发现 %d 只持仓超额（孤儿/未知），G2 不主动处理，仅预警" % len(report["orphans"]))
    if report["cross_strategy"]:
        # 提示类进 notes（不进 issues，防 de_guard_runner 以 issues 非空每日误报）
        report.setdefault("notes", [])
        report["notes"].append("账户含 %d 只融合ENS 持仓（同账号，非 G2 孤儿，仅提示）" % len(report["cross_strategy"]))

    # ④ 资产
    report["asset"] = read_asset(date) or {}

    # ⑤ 资金池滚动（DE 体检 P1-2）：初始 + 累计已实现 + 当前持仓浮盈 → save_g2_capital
    report["capital_rolled"] = _roll_g2_capital(date)

    # 输出
    out_dir = os.path.join(G.DATA_DIR, "reconcile_g2")
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "reconcile_g2_%s.json" % date)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    mp = os.path.join(out_dir, "reconcile_g2_%s.md" % date)
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# G2 日终对账 %s\n\n" % date)
        f.write("- 账号 %s\n\n" % G.ACCOUNT_ID)
        f.write("## fills（%d 条）\n" % len(fill_list))
        for k, v in sorted(by_status.items()):
            f.write("- %s: %d\n" % (k, v))
        f.write("\n## pending\n")
        f.write("- %d 条（%s）\n" % (len(pending), "有风险" if pending else "正常"))
        f.write("\n## G2 账本 vs 账户持仓（差额=账户-账本）\n")
        for code in sorted(set(ledger) | set(acct)):
            lv = ledger.get(code, 0)
            av = acct.get(code, 0)
            diff = av - lv
            flag = "✓" if diff == 0 else ("⚠ 账户多 %d" % diff if diff > 0 else "⚠ 账户少 %d" % -diff)
            f.write("- %s G2账本=%d 账户=%d %s\n" % (code, lv, av, flag))
        if report.get("snapshot_missing"):
            f.write("⚠ SNAPSHOT_MISSING: 账户快照不存在，差额与孤儿判定跳过，请人工核对上一交易日快照。\n")
        f.write("\n## 持仓差额/孤儿\n")
        if report["cross_strategy"]:
            f.write("**同账号融合ENS 持仓（非 G2 孤儿）**\n")
            for o in report["cross_strategy"]:
                f.write("- %s %d 股：%s\n" % (o["code"], o["vol"], o["hint"]))
        if report["orphans"]:
            for o in report["orphans"]:
                f.write("- %s %d 股：%s\n" % (o["code"], o["vol"], o["hint"]))
        elif not report["cross_strategy"]:
            f.write("- 无\n")
        f.write("\n## 问题\n")
        if report["issues"]:
            for i in report["issues"]:
                f.write("- ⚠ %s\n" % i)
        else:
            f.write("- 无\n")
    print("对账完成: %s" % jp)
    print("fills=%d pending=%d 孤儿=%d 问题=%d" % (
        len(fill_list), len(pending), len(report["orphans"]), len(report["issues"])))
    for i in report["issues"]:
        print("  [ISSUE] %s" % i)
    # 飞书通知（对账结果摘要；有 issue 标 ⚠）
    try:
        issue_txt = "\n".join("⚠ " + i for i in report["issues"]) if report["issues"] else "无问题"
        notify_feishu("【G2对账 %s】fills=%d pending=%d 孤儿=%d 问题=%d\n%s" % (
            date, len(fill_list), len(pending), len(report["orphans"]), len(report["issues"]), issue_txt))
    except Exception as e:
        print("[通知] 对账飞书异常: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
