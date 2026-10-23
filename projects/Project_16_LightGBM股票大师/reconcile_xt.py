# coding: utf-8
"""G2 / ENS 账本 vs 账户持仓对账（同账号三策略并存，归属靠各自账本）。

背景
----
2026-10-22 起 V1.3 / G2 / ENS 三策略同跑 67014907，各写独立成交账本：
  V1.3 → data/qmt_trade_log.csv
  G2   → D:/QMT_POOL/g2_bridge/g2_trade_log.csv
  ENS  → D:/QMT_POOL/p16_ensemble_bridge/ens_trade_log.csv

内置风控（V13 / G2风控 / ENS风控）卖出后**无法写文件**（QMT 沙箱），
所以外部账本会滞后于账户 → 本脚本负责发现并提示差异。

判定（AGENTS 资金分配红线）
  - 账本有、账户少 → 疑风控卖出未回写（可用 orders 归档回补）
  - 账户有、三份账本都无 → 孤儿持仓，**不纳管**，只告警人工裁决

用法：
  python reconcile_xt.py --strategy g2
  python reconcile_xt.py --strategy ens
  python reconcile_xt.py --strategy all
产物：data/reconcile/<strategy>_reconcile_<date>.json / .md
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xt_exec
import g2_config as G
import g2_ens_config as E

OUT_DIR = os.path.join(HERE, "data", "reconcile")

STRATEGIES = {
    "g2": ("G2", G),
    "ens": ("融合", E),
}


def _reconcile_one(key, cfg, all_ledger_codes):
    print("")
    print("== %s 对账（账号 %s）==" % (STRATEGIES[key][0], cfg.ACCOUNT_ID))
    try:
        trader, account = xt_exec.connect(cfg)
    except Exception as e:
        print("!! 连接失败: %r" % (e,))
        return {"strategy": key, "connected": False, "error": repr(e)}
    try:
        res = xt_exec.reconcile(cfg, trader, account)
        asset = trader.query_stock_asset(account)
    finally:
        try:
            trader.stop()
        except Exception:
            pass

    ledger = res["ledger"]
    account_pos = res["account"]
    # 孤儿 = 账户有、但**三份账本都没有**的票（属于 V1.3 或他人）
    orphans_real = sorted(c for c in account_pos if c not in all_ledger_codes)
    doc = {
        "strategy": key,
        "account_id": cfg.ACCOUNT_ID,
        "date": time.strftime("%Y%m%d"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "connected": True,
        "ledger": {c: v for c, v in ledger.items()},
        "ledger_count": len(ledger),
        "deltas": res["deltas"],
        "issues": res["issues"],
        "orphans_not_in_any_ledger": orphans_real,
        "orphans_count": len(orphans_real),
        "total_asset": round(asset.total_asset, 2) if asset else None,
        "cash": round(asset.cash, 2) if asset else None,
        "market_value": round(asset.market_value, 2) if asset else None,
    }
    print("账本持仓 %d 只 | 差额 %d 只 | 非本策略/孤儿 %d 只"
          % (len(ledger), len(res["deltas"]), len(orphans_real)))
    for i in res["issues"]:
        print("  !! " + i)
    if orphans_real:
        print("  [孤儿] %s（三份账本均无记录，绝不纳管）" % ",".join(orphans_real))
    return doc


def _write_md(docs, date):
    os.makedirs(OUT_DIR, exist_ok=True)
    lines = ["# G2 / ENS 账本对账 %s" % date, "",
             "> 生成 %s　账号 %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), G.ACCOUNT_ID), ""]
    for d in docs:
        name = STRATEGIES.get(d.get("strategy"), (d.get("strategy"),))[0]
        lines.append("## %s" % name)
        lines.append("")
        if not d.get("connected"):
            lines.append("- ❌ 连接失败：%s" % d.get("error"))
            lines.append("")
            continue
        lines += [
            "- 账本持仓：**%d 只**" % d["ledger_count"],
            "- 持仓差额：**%d 只**" % len(d["deltas"]),
            "- 孤儿（三份账本均无）：**%d 只**" % d["orphans_count"],
            "- 总资产：%s / 现金：%s / 市值：%s" % (d["total_asset"], d["cash"], d["market_value"]),
            "",
        ]
        if d["ledger"]:
            lines += ["| 代码 | 账本股数 | 成本 |", "|---|---|---|"]
            for c, v in sorted(d["ledger"].items()):
                lines.append("| %s | %d | %.4f |" % (c, v["vol"], v["cost"]))
            lines.append("")
        if d["issues"]:
            lines.append("**差异明细**")
            for i in d["issues"]:
                lines.append("- " + i)
            lines.append("")
        if d["orphans_not_in_any_ledger"]:
            lines.append("**孤儿持仓**（不纳管，需人工裁决）：%s" %
                         ", ".join(d["orphans_not_in_any_ledger"]))
            lines.append("")
    path = os.path.join(OUT_DIR, "reconcile_%s.md" % date)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    ap = argparse.ArgumentParser(description="G2/ENS 账本 vs 账户对账")
    ap.add_argument("--strategy", default="all", choices=["g2", "ens", "all"])
    args = ap.parse_args()

    keys = list(STRATEGIES) if args.strategy == "all" else [args.strategy]

    # V1.3 账本也算进「已有归属」，避免把它的票误判孤儿
    all_codes = set()
    for k in STRATEGIES:
        try:
            all_codes |= set(xt_exec.ledger_from_trades(STRATEGIES[k][1]).keys())
        except Exception:
            pass
    try:
        import qmt_config as C
        all_codes |= set(xt_exec.ledger_from_trades(C).keys())
    except Exception as e:
        print("  (V1.3 账本未纳入归属判定: %r)" % (e,))

    date = time.strftime("%Y%m%d")
    docs = []
    for k in keys:
        docs.append(_reconcile_one(k, STRATEGIES[k][1], all_codes))

    os.makedirs(OUT_DIR, exist_ok=True)
    jp = os.path.join(OUT_DIR, "reconcile_%s_%s.json" % (args.strategy, date)
                      if args.strategy != "all" else "reconcile_%s.json" % date)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    mp = _write_md(docs, date)
    print("")
    print("[输出] %s" % jp)
    print("[输出] %s" % mp)

    bad = [d for d in docs if not d.get("connected") or d.get("deltas")]
    if bad:
        print("[WARN] 存在连接失败或持仓差额，需人工核查")
        return 1
    print("[OK] 对账通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
