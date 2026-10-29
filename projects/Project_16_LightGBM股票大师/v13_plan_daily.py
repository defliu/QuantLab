# coding=utf-8
"""V1.3 每日「只出计划」入口（2026-10-29 立）。

用途：给 QMT 内置执行壳 STRATEGY_P16_V13_EXEC 准备信号。
链路：market_tier（腾讯实时定 T 档）→ rebalance_daily.py --plan-only
      → data/rebalance_v13/rebalance_v13_<date>.plan.json → hub 58712 → 内置 passorder

为什么不用 rebalance_daily 的 --live：xtquant 在 2.1.33 上已不可用（XtMiniQmt 不再自带、
connect 返 rc=-1），外部下单腿整体失效；改成「内置执行」后，外部只负责算计划。

失败不静默：任何一步失败都打印明确原因并返回非 0，由任务日志留痕（不产生半截计划）。
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _log(msg):
    print("[V13PLAN] " + str(msg))
    sys.stdout.flush()


def _today():
    return time.strftime("%Y%m%d")


def main():
    # 1) T 档（确定性脚本，禁止自行判断）
    try:
        out = subprocess.check_output([PY, os.path.join(HERE, "market_tier.py"), "--json"],
                                      cwd=HERE, stderr=subprocess.STDOUT)
        txt = out.decode("utf-8", "replace").strip().splitlines()[-1]
        tier = int(json.loads(txt).get("tier"))
    except Exception as e:
        _log("FAIL market_tier: %s" % (str(e)[:160],))
        return 2
    if tier not in (0, 1, 2):
        _log("FAIL T 档异常：%r" % (tier,))
        return 2
    _log("T 档 tier=%d" % tier)

    # 2) 只出计划（不抢 LIVE 锁、不连 xtquant）
    cmd = [PY, os.path.join(HERE, "rebalance_daily.py"),
           "--date", _today(), "--top", str(tier), "--plan-only"]
    rc = subprocess.call(cmd, cwd=HERE)
    _log("rebalance_daily --plan-only rc=%d" % rc)
    return 0 if rc == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
