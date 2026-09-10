# coding: utf-8
"""G2 每日 positions_cfg（成本锚）自动生成 —— 桥内止损/止盈/对账账本依赖。

背景（2026-09-02 发现）：cmd/positions_cfg_<date>.json 之前只能手动生成（9/1 手动写过一次，
9/2 建仓后未生成），桥 init 读不到成本锚 → 风控成本缺失。本脚本每日自动生成。

数据源：
  1) G2 持仓 code 集合：data/rebalance_g2/g2_hold_dates.json（建仓日记录 = G2 纳管持仓）
  2) 成本/数量：state/positions_<date>.json（桥每日导出账户全量持仓，avg_price=含费成本）

只写 G2 自己的持仓（绝不把账户里他人策略/孤儿的票写进 G2 成本锚），account_id 戳校验。

用法：
  python gen_positions_cfg_g2.py                # 今天（date=今日）
  python gen_positions_cfg_g2.py --date 20260902
  python gen_positions_cfg_g2.py --dry-run      # 只打印不写
退出码：0=已写/无变化/正常跳过；1=异常（账户持仓缺失等需人工核查）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g2_config as G
from qmt_bridge_client import read_positions, _atomic_write_json, _positions_cfg_path, STATE_DIR, _read_json


def _read_latest_positions(date):
    """优先当日 positions 快照；缺失时回退最近一份（T-20260910：09:00 盘前生成用。
    桥约 15:00 才导出当日快照，盘前当日文件必不存在 → 若无回退则 SKIP 不写成本锚 →
    桥内风控全天静默。隔夜持仓不变，昨日快照成本有效；账号戳不符的快照绝不采用）。"""
    pos = read_positions(date) or {}
    if pos and pos.get("positions"):
        return pos
    try:
        names = [n for n in os.listdir(STATE_DIR)
                 if n.startswith("positions_") and n.endswith(".json")]
        names.sort(reverse=True)
        for n in names:
            p = _read_json(os.path.join(STATE_DIR, n))
            if p and p.get("positions") and str(p.get("account_id", "") or "") == G.ACCOUNT_ID:
                print("  [FALLBACK] 当日持仓快照缺失，回退 %s（隔夜持仓不变，成本有效）" % n)
                return p
    except Exception:
        pass
    return {}


def _load_hold_codes():
    """G2 持仓 code 集合（g2_hold_dates.json 的 key；文件缺失返回空集）。"""
    try:
        if os.path.exists(G.HOLD_DATES_FILE):
            with open(G.HOLD_DATES_FILE, encoding="utf-8") as f:
                d = json.load(f)
            return set(str(k) for k in (d.get("hold_dates", {}) or {}).keys())
    except Exception:
        pass
    return set()


def main_with_date(date, dry_run=False):
    """按指定 date 生成 positions_cfg（供 CLI 与 rebalance_g2 复用）。返回退出码。"""
    print("== G2 positions_cfg 生成（%s）== account=%s" % (date, G.ACCOUNT_ID))

    hold_codes = _load_hold_codes()
    if not hold_codes:
        print("[SKIP] g2_hold_dates.json 无 G2 持仓记录（空仓或未初始化），不写成本锚")
        return 0

    pos = _read_latest_positions(date)
    if not pos or not pos.get("positions"):
        print("[SKIP] state/ 无可用账户持仓快照（桥未导出），不写成本锚")
        return 0
    acct = {p.get("code", ""): p for p in pos.get("positions", [])}

    rows = []
    for code in sorted(hold_codes):
        p = acct.get(code)
        if p is None:
            print("  [WARN] %s 在 hold_dates 但账户无该持仓（可能已清，建仓日未移除？），跳过" % code)
            continue
        vol = int(p.get("volume", 0) or 0)
        cost = float(p.get("avg_price", 0) or 0)
        if vol <= 0 or cost <= 0:
            print("  [WARN] %s 账户持仓 vol=%d cost=%.4f 非法，跳过" % (code, vol, cost))
            continue
        rows.append({"code": code, "cost": round(cost, 4), "vol": vol})

    if not rows:
        print("[SKIP] G2 持仓在账户中都无有效仓位，不写成本锚")
        return 0

    payload = {
        "account_id": G.ACCOUNT_ID,
        "strategy": G.STRATEGY,
        "date": date,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "positions": rows,
    }
    print("  将写 %d 条：%s" % (len(rows), ", ".join("%s %d@%.4f" % (r["code"], r["vol"], r["cost"]) for r in rows)))
    if dry_run:
        print("[DRY-RUN] 未写")
        return 0

    path = _positions_cfg_path(date)
    _atomic_write_json(path, payload)
    print("[OK] 已写 %s" % path)
    return 0


def main():
    ap = argparse.ArgumentParser(description="G2 每日 positions_cfg 成本锚自动生成")
    ap.add_argument("--date", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打印不写")
    args = ap.parse_args()
    date = args.date or time.strftime("%Y%m%d")
    return main_with_date(date, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
