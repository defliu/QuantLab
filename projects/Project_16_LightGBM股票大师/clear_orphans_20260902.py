# -*- coding: utf-8 -*-
"""2026-09-02 清仓指令生成脚本（备用：运行本脚本才写 cmd/orders_20260902.json，未运行=不触发）。

用途：清掉 2026-09-01 大QMT 调试遗留的 500 股孤儿仓（T+1 锁定，09-02 开盘后可卖）。
持仓（今日 09-01 状态）：
  600028.SH 200 股（成本@5.6005 含费）
  601988.SH 200 股（成本@6.691 含费）
  601398.SH 100 股（成本@8.2243 含费，撤单测试意外成交仓）

运行（09-02 开盘后）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe clear_orphans_20260902.py

说明：写 3 条 SELL 指令（prtype=5 最新价市价卖出）到 cmd/orders_20260902.json，
桥 09-02 读取后先卖后买逻辑处理；重复运行有幂等保护（已存在则跳过）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qmt_bridge_client import write_orders, read_positions, fetch_price, _cmd_path

DATE = "20260902"
ACCOUNT_ID = "70180771"

CLEAR = [
    {"code": "600028.SH", "vol": 200, "cost": 5.6005},
    {"code": "601988.SH", "vol": 200, "cost": 6.691},
    {"code": "601398.SH", "vol": 100, "cost": 8.2243},
]


def main():
    # 幂等：orders_<DATE>.json 已存在则跳过（防重复写）
    orders_path = _cmd_path(DATE)
    if os.path.exists(orders_path):
        print("[SKIP] %s 已存在，如需重写请先删除该文件" % orders_path)
        return 0

    # 校验昨日（09-01）状态确实还有这 500 股，防止价格/数量口径错
    try:
        pos = read_positions("20260901") or {}
        pos_map = {p.get("code", ""): int(p.get("volume", 0) or 0) for p in pos.get("positions", [])}
    except Exception as e:
        print("[WARN] 读取 09-01 positions 失败（不影响写单）：%s" % e)
        pos_map = {}
    for c in CLEAR:
        cur = pos_map.get(c["code"], 0)
        if cur < c["vol"]:
            print("[WARN] %s 现持 %d 股 < 计划卖 %d 股（可能已被风控卖出/变化），按实际 %d 卖"
                  % (c["code"], cur, c["vol"], cur))
            c["vol"] = cur
    if sum(c["vol"] for c in CLEAR) <= 0:
        print("[ABORT] 无持仓可清，不写指令")
        return 0

    # 取实时价做参考（prtype=5 最新价成交，price 仅作记录）
    orders = []
    for i, c in enumerate(CLEAR, 1):
        if c["vol"] <= 0:
            continue
        try:
            p = fetch_price(c["code"]) or 0.01
        except Exception:
            p = c["cost"]
        orders.append({
            "action": "SELL",
            "code": c["code"],
            "vol": c["vol"],
            "price": p,
            "reason": "清仓孤儿仓(09-01调试遗留T+1)",
            "strategy_order_id": "P16_%s_%04d" % (DATE, i),
        })

    seq = write_orders(orders, date=DATE, account_id=ACCOUNT_ID)
    print("[OK] 已写 orders_%s.json seq=%d，%d 条 SELL：" % (DATE, seq, len(orders)))
    for o in orders:
        print("     %s %s %d股 @%.3f（成本 %.4f，%s）"
              % (o["action"], o["code"], o["vol"], o["price"],
                 next((c["cost"] for c in CLEAR if c["code"] == o["code"]), 0), o["strategy_order_id"]))
    print("[DONE] 09-02 开盘后桥处理即市价卖出清仓。核对：fills 应出现 3 条 SELL FILLED。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
