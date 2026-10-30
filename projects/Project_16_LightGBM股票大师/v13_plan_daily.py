# coding=utf-8
"""V1.3 每日「只出计划」入口（2026-10-29 立）。

用途：给 QMT 内置执行壳 STRATEGY_P16_V13_EXEC 准备信号。
链路：market_tier（腾讯实时定 T 档）→ rebalance_daily.py --plan-only
      → data/rebalance_v13/rebalance_v13_<date>.plan.json → hub 58610/signal/v13 → 内置 passorder

为什么不用 rebalance_daily 的 --live：xtquant 在 2.1.33 上已不可用（XtMiniQmt 不再自带、
connect 返 rc=-1），外部下单腿整体失效；改成「内置执行」后，外部只负责算计划。

**时序约束（2026-10-30 立）**：执行壳窗口是 09:46–09:56（`EXEC_HM_START/END`，见
build/strategy_p16_v13_exec.py）。本脚本必须**在窗口打开之前**把计划落盘，否则执行壳
每轮都只能打「非执行窗口/订单为空」，当日必然零成交。故：
  · 计划任务触发时点 = 09:42（留 4 分钟余量；实测本脚本全程 ~6 秒）；
  · 本脚本收尾自检：若落盘时刻已过 `EXEC_HM_START`，推告警「本轮必然零成交」，
    不静默成功——这正是 2026-10-30 的事故形态（selection 任务当天 12:13 才注册，
    计划 12:15 才落盘，窗口早过，当日 V13 三腿全空）。

失败不静默：任何一步失败都打印明确原因并返回非 0，由任务日志留痕（不产生半截计划）。
退出码：0 正常 / 2 非交易日或 T 档异常 / 3 计划生成失败 / 4 计划晚于执行窗口
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# 执行壳窗口（必须与 build/strategy_p16_v13_exec.py 的 EXEC_HM_START/END 一致）。
# 这里只用来做「计划是否来得及」的自检，不参与任何下单决策。
EXEC_HM_START = 946
PLAN_DIR = os.path.join(HERE, "data", "rebalance_v13")


def _log(msg):
    print("[V13PLAN] " + str(msg))
    sys.stdout.flush()


def _today():
    return time.strftime("%Y%m%d")


def _hhmm():
    return int(time.strftime("%H%M"))


def _plan_path(date_str):
    return os.path.join(PLAN_DIR, "rebalance_v13_%s.plan.json" % date_str)


def _push(title, body):
    """告警推送（best-effort，失败不阻断）。"""
    try:
        env = dict(os.environ)
        env["LARK_SRC_TAG"] = "[QD]"
        subprocess.call([PY, os.path.join(HERE, "push_alert_card.py"),
                         "--title", title, "--body", body, "--level", "warn"],
                        cwd=HERE, env=env, timeout=60)
    except Exception as e:
        _log("PUSH-FAIL %s" % (str(e)[:100],))


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
    if rc != 0:
        return 3

    # 3) 时序自检：计划必须在执行壳窗口打开之前落盘，否则当日必然零成交。
    #    「落盘了但晚了」是一种静默失败——盘上有计划、日志有 PLAN 候选，唯独窗口已过，
    #    事后极难看出根因（2026-10-30 即如此：计划 12:15 落盘，窗口 946-956 早过）。
    path = _plan_path(_today())
    hhmm = _hhmm()
    if not os.path.exists(path):
        _log("WARN 计划文件未生成：%s" % path)
        _push("V1.3 计划未生成", "rebalance_daily --plan-only 返回 0 但产物不存在：%s" % path)
        return 3
    if hhmm >= EXEC_HM_START:
        _log("FAIL 计划落盘晚于执行窗口（现在 %04d >= 窗口起点 %04d）——本轮 V1.3 必然零成交"
             % (hhmm, EXEC_HM_START))
        _push("V1.3 计划晚于执行窗口",
              "计划已生成但落盘时刻 %04d 已过执行窗口起点 %04d，内置执行壳不会再下单。"
              "排查计划任务触发时点（应 09:42 之前）与上游候选生成（09:36）。" % (hhmm, EXEC_HM_START))
        return 4
    _log("OK 计划已就绪 %s（%04d，距窗口起点 %04d 还有 %d 分钟）"
         % (path, hhmm, EXEC_HM_START, (EXEC_HM_START // 100 - hhmm // 100) * 60
            + (EXEC_HM_START % 100 - hhmm % 100)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
