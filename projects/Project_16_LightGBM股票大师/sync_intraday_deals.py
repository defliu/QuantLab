# coding: utf-8
"""内置风控卖出 → 外部账本补记（拍板 A 的必要配套）。

问题
----
拍板 A 决定风控内置（QMT 客户端 tick 级止损/止盈/追盈），但内置策略**无法写文件**
（QMT 沙箱），所以它卖出后外部账本 g2_trade_log.csv / ens_trade_log.csv 感知不到
→ 账本净额 > 账户持仓，次日换仓会误判"仍持有"而漏卖 / 或按错误成本算盈亏。

方案
----
每日 09:00 盘前跑一次：
  1) 读当日归档 trades_<前一交易日>.csv（archive_daily_orders.py 产物）或
     query_stock_orders 当日委托（若隔日归档缺失）
  2) 逐笔找「账户已减、账本未减」的差额
  3) 若能在归档里找到对应的 SELL 记录（remark 为风控标记 / 数量匹配）→ 补记进账本，
     reason=RISK_INTRADAY
  4) 找不到 → **不猜**，只告警，等人工裁决（宁缺勿错）

用法：
  python sync_intraday_deals.py            # 默认对 G2 + ENS
  python sync_intraday_deals.py --dry-run  # 只报告不写账本
  python sync_intraday_deals.py --strategy g2
"""
import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xt_exec
import g2_config as G
import g2_ens_config as E

ARCHIVE = os.path.join(HERE, "data", "archive")
STRATEGIES = {"g2": ("G2", G), "ens": ("融合", E)}


def _prev_trade_day_archive():
    """找最近一份 trades_*.csv 归档（按文件名日期倒序）。"""
    if not os.path.isdir(ARCHIVE):
        return None
    files = sorted([f for f in os.listdir(ARCHIVE)
                    if f.startswith("trades_") and f.endswith(".csv")], reverse=True)
    return os.path.join(ARCHIVE, files[0]) if files else None


def _load_archive_sells(path):
    """读归档成交里的卖出记录 → [{code, vol, price, remark}]。"""
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            # order_type: 24=卖出（xtconstant.STOCK_SELL）
            if str(r.get("order_type", "")) != "24":
                continue
            try:
                vol = int(float(r.get("vol") or 0))
                px = float(r.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if vol > 0:
                out.append({"code": (r.get("code") or "").strip(), "vol": vol,
                            "price": px, "remark": r.get("remark", ""),
                            "time": r.get("traded_time", ""), "src": os.path.basename(path)})
    return out


def _sync_one(key, cfg, archive_sells, dry_run):
    name = STRATEGIES[key][0]
    print("")
    print("== %s 补记检查（账号 %s）==" % (name, cfg.ACCOUNT_ID))
    try:
        trader, account = xt_exec.connect(cfg)
    except Exception as e:
        print("!! 连接失败: %r" % (e,))
        return {"strategy": key, "connected": False, "error": repr(e), "backfilled": []}
    try:
        res = xt_exec.reconcile(cfg, trader, account)
    finally:
        try:
            trader.stop()
        except Exception:
            pass

    backfilled, unresolved = [], []
    account_pos = res["account"]
    for code, delta in res["deltas"].items():
        if delta <= 0:
            # 账户多（可能买入未落账）——不在本脚本职责内，只报告
            unresolved.append({"code": code, "delta": delta,
                               "reason": "账户多于账本（疑买入未落账），需人工核查"})
            continue
        # 账本多 → 疑风控卖出，从归档里找该 code 的 SELL
        cands = [s for s in archive_sells if s["code"] == code]
        need = delta
        used = 0
        for s in cands:
            if used >= need:
                break
            take = min(s["vol"], need - used)
            if take <= 0:
                continue
            if dry_run:
                print("  [DRY] 将补记 SELL %s %d股 @%.4f（归档 %s）" % (code, take, s["price"], s["src"]))
            else:
                xt_exec.append_trade(cfg, code, "SELL", take, s["price"],
                                     order_id="", reason="RISK_INTRADAY")
                print("  [补记] SELL %s %d股 @%.4f（归档 %s）" % (code, take, s["price"], s["src"]))
            backfilled.append({"code": code, "vol": take, "price": s["price"],
                               "src": s["src"]})
            used += take
        if used < need:
            left = need - used
            unresolved.append({"code": code, "delta": left,
                               "reason": "账本多 %d 股但归档无对应卖出（隔日数据已清），需人工核对 QMT 客户端历史成交" % left})
            print("  !! %s 差额 %d 股无法从归档补记（需人工核对）" % (code, left))

    doc = {
        "strategy": key, "account_id": cfg.ACCOUNT_ID,
        "date": time.strftime("%Y%m%d"),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "connected": True, "dry_run": bool(dry_run),
        "ledger_before": res["ledger"],
        "deltas": res["deltas"],
        "backfilled": backfilled,
        "unresolved": unresolved,
        "orphans_not_in_ledger": res["orphans"],
    }
    return doc


def main():
    ap = argparse.ArgumentParser(description="内置风控卖出 → 外部账本补记")
    ap.add_argument("--strategy", default="all", choices=["g2", "ens", "all"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    apath = _prev_trade_day_archive()
    sells = _load_archive_sells(apath)
    print("归档源: %s（卖出记录 %d 条）" % (apath or "无", len(sells)))
    if not apath:
        print("  !! 无归档文件 → 无法补记（请先确认 archive_daily_orders.py 已跑过）")

    keys = list(STRATEGIES) if args.strategy == "all" else [args.strategy]
    docs = [_sync_one(k, STRATEGIES[k][1], sells, args.dry_run) for k in keys]

    out_dir = os.path.join(HERE, "data", "reconcile")
    os.makedirs(out_dir, exist_ok=True)
    jp = os.path.join(out_dir, "sync_deals_%s.json" % time.strftime("%Y%m%d"))
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(docs, f, ensure_ascii=False, indent=2)
    print("")
    print("[输出] %s" % jp)

    n_unres = sum(len(d.get("unresolved", [])) for d in docs if d.get("connected"))
    n_back = sum(len(d.get("backfilled", [])) for d in docs if d.get("connected"))
    print("[汇总] 补记 %d 笔，未解决 %d 笔" % (n_back, n_unres))
    return 1 if n_unres else 0


if __name__ == "__main__":
    sys.exit(main())
