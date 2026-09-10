# coding: utf-8
"""融合(ensemble)（大QMT 文件桥）日终对账 —— 与 G2 桥（reconcile_g2.py）**完全隔离**。

核对：
  1) fills 每笔状态（FILLED/CANCELED/REJECTED/UNCONFIRMED…）
  2) 桥心跳 pending 是否残留（活跃单未收尾）
  3) 融合账本（positions_cfg/fills 推导）vs 账户持仓 → 识别孤儿仓（账户有票但融合账本无 =
     调试遗留/他人策略，融合不纳管不误杀，仅报告）
  4) 资产快照

用法：
  python reconcile_ens.py                # 今天
  python reconcile_ens.py --date 20260902
输出：data/reconcile_g2_ens/reconcile_g2_ens_<date>.md/.json
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g2_ens_config as G
from qmt_bridge_client_ens import read_fills, read_positions, read_asset, read_heart, positions_from_fills, notify_feishu

TERMINAL_STATUS = ("FILLED", "PARTIAL_FILLED", "CANCELED", "REJECTED", "UNCONFIRMED", "ABANDONED")


def main():
    ap = argparse.ArgumentParser(description="融合日终对账")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    date = args.date or time.strftime("%Y%m%d")

    # 对账前刷新当日 positions_cfg 成本锚（rebalance --live 已生成，
    # 但风控卖出/当日变化后需按最新账户持仓校准；失败不阻断对账）
    try:
        import gen_positions_cfg_ens as GPC
        _rc = GPC.main_with_date(date)
        if _rc != 0:
            print("[WARN] positions_cfg 刷新异常 exit=%d，对账用既有账本" % _rc)
    except Exception as e:
        print("[WARN] positions_cfg 刷新失败（不影响对账）: %s" % e)

    report = {
        "date": date, "account_id": G.ACCOUNT_ID,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fills": [], "pending": [], "orphans": [], "ledger": {}, "asset": {},
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

    # ③ 融合账本 vs 账户持仓 → 孤儿仓
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
    # 差额对账：超额=账户多（孤儿/重复建仓），不足=账本多（卖出未回写/丢失）
    deltas = {}
    for code in set(ledger) | set(acct):
        diff = acct.get(code, 0) - ledger.get(code, 0)
        if diff:
            deltas[code] = diff
    report["deltas"] = deltas
    for code, diff in deltas.items():
        if diff > 0:
            report["orphans"].append({"code": code, "vol": diff,
                                      "hint": "账户比融合账本多 %d 股（孤儿/重复建仓，融合不主动处理，仅预警）" % diff})
            report["issues"].append("持仓差额: %s 账户比账本多 %d 股" % (code, diff))
        else:
            report["issues"].append("持仓差额: %s 账本比账户多 %d 股（卖出未回写/丢失，需排查）" % (code, -diff))
    if report["orphans"]:
        report["issues"].append("发现 %d 只持仓超额（孤儿/重复），融合不主动处理，仅预警" % len(report["orphans"]))

    # ④ 资产
    report["asset"] = read_asset(date) or {}

    # 输出
    out_dir = os.path.join(G.DATA_DIR, "reconcile_g2_ens")
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "reconcile_g2_ens_%s.json" % date)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    mp = os.path.join(out_dir, "reconcile_g2_ens_%s.md" % date)
    with open(mp, "w", encoding="utf-8") as f:
        f.write("# 融合日终对账 %s\n\n" % date)
        f.write("- 账号 %s\n\n" % G.ACCOUNT_ID)
        f.write("## fills（%d 条）\n" % len(fill_list))
        for k, v in sorted(by_status.items()):
            f.write("- %s: %d\n" % (k, v))
        f.write("\n## pending\n")
        f.write("- %d 条（%s）\n" % (len(pending), "有风险" if pending else "正常"))
        f.write("\n## 融合账本 vs 账户持仓（差额=账户-账本）\n")
        for code in sorted(set(ledger) | set(acct)):
            lv = ledger.get(code, 0)
            av = acct.get(code, 0)
            diff = av - lv
            flag = "✓" if diff == 0 else ("⚠ 账户多 %d" % diff if diff > 0 else "⚠ 账户少 %d" % -diff)
            f.write("- %s 融合账本=%d 账户=%d %s\n" % (code, lv, av, flag))
        f.write("\n## 持仓差额/孤儿\n")
        if report["orphans"]:
            for o in report["orphans"]:
                f.write("- %s %d 股：%s\n" % (o["code"], o["vol"], o["hint"]))
        else:
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
        notify_feishu("【融合对账 %s】fills=%d pending=%d 孤儿=%d 问题=%d\n%s" % (
            date, len(fill_list), len(pending), len(report["orphans"]), len(report["issues"]), issue_txt))
    except Exception as e:
        print("[通知] 对账飞书异常: %s" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
