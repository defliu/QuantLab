# coding=utf-8
"""V1.3 每日「只出计划」入口（2026-10-29 立）。

用途：给 QMT 内置执行壳 STRATEGY_P16_V13_EXEC 准备信号。
链路：market_tier（腾讯实时定 T 档）→ rebalance_daily.py --plan-only
      → data/rebalance_v13/rebalance_v13_<date>.plan.json → hub 58610/signal/v13 → 内置 passorder

为什么不用 rebalance_daily 的 --live：xtquant 在 2.1.33 上已不可用（XtMiniQmt 不再自带、
connect 返 rc=-1），外部下单腿整体失效；改成「内置执行」后，外部只负责算计划。

**时序约束（2026-10-30 立，同日修订）**：执行壳窗口是 09:46–09:56（`EXEC_HM_START/END`，
见 build/strategy_p16_v13_exec.py）。本脚本必须**在窗口打开之前**把计划落盘，否则执行壳
每轮都只能打「非执行窗口/订单为空」，当日必然零成交。故：
  · 计划任务触发时点 = 09:42（留 4 分钟余量；实测本脚本全程 ~6 秒）；
  · **时间门放在落盘之前**：过窗口就直接 return 4，**不调用 rebalance_daily**、一个字节都不写。
    早期版本把判定放在 subprocess.call 之后，属「先破坏、后报警」——2026-10-30 19:22 系统
    时钟前跳触发补跑，先由 rebalance_daily 把当日计划从 tier2/3腿 覆盖成 tier1/0腿，
    然后才返回 4 说"晚于窗口"。计划是 hub/执行壳/人肉复盘的输入，写坏了就是已经造成损害。

失败不静默：任何一步失败都打印明确原因并返回非 0，由任务日志留痕（不产生半截计划）。
退出码：0 正常 / 2 非交易日或 T 档异常 / 3 计划生成失败 / 4 已过执行窗口（跳过生成）
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

    # 2) ★ 时间门必须在**落盘之前**（2026-10-30 审计 P1，实测踩到）：
    #    早期把它放在 subprocess.call 之后，是「先破坏、后报警」——
    #    19:22 那次系统时钟前跳触发的补跑，先由 rebalance_daily 把当日计划从
    #    tier2/5档/3腿 覆盖成 tier1/1档/0腿（688B→313B），然后才返回 4 说"晚于窗口"。
    #    计划文件是下游 hub / 执行壳 / 人肉复盘的输入，写坏了就已经造成损害。
    #    现在：过窗口直接不调用 rebalance_daily，一个字节都不写。
    hhmm = _hhmm()
    if hhmm >= EXEC_HM_START:
        _log("SKIP 已过执行窗口（现在 %04d >= 窗口起点 %04d）——不生成计划，避免覆盖当日正式计划"
             % (hhmm, EXEC_HM_START))
        _push("V1.3 计划跳过（补跑晚于窗口）",
              "本轮触发时刻 %04d 已过执行窗口起点 %04d，已跳过生成（不覆盖已有计划）。"
              "若这是补跑，属正常跳过；若每日都如此，排查计划任务触发时点（应 09:42）。"
              % (hhmm, EXEC_HM_START))
        return 4

    # 3) 只出计划（不抢 LIVE 锁、不连 xtquant）
    cmd = [PY, os.path.join(HERE, "rebalance_daily.py"),
           "--date", _today(), "--top", str(tier), "--plan-only"]
    rc = subprocess.call(cmd, cwd=HERE)
    _log("rebalance_daily --plan-only rc=%d" % rc)
    if rc != 0:
        return 3

    # 4) 落盘后校验：命令返回 0 但产物不存在，属静默失败，必须显式报
    path = _plan_path(_today())
    if not os.path.exists(path):
        _log("WARN 计划文件未生成：%s" % path)
        _push("V1.3 计划未生成", "rebalance_daily --plan-only 返回 0 但产物不存在：%s" % path)
        return 3
    _log("OK 计划已就绪 %s（%04d，距窗口起点 %04d 还有 %d 分钟）"
         % (path, hhmm, EXEC_HM_START, (EXEC_HM_START // 100 - hhmm // 100) * 60
            + (EXEC_HM_START % 100 - hhmm % 100)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
