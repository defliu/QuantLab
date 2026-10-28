# -*- coding: utf-8 -*-
r"""qmt_autodeploy 时段闸「沙箱免闸」判定的单元回归（2026-10-29）。

为什么要单独测：gate 的输入是**外部传入的路径串**（`--qmt-dir`），而 AGENTS 有一条硬规则
——外部路径必须先归一，否则 `D:/x` 与 `D:\x` 会静默失配，把沙箱当成主号去拦。这类失配
不抛异常、只在交易日白天表现为「明明该部署却 SKIP 时段闸」，极难从日志反推。
"""
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, r"D:\QuantLab\projects\Project_16_LightGBM股票大师")

import qmt_autodeploy as ad   # noqa: E402

fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


S = r"D:\QMT交易端模拟"

check("G1 sandbox backslash path is exempt", ad.is_gate_exempt(S))
check("G2 forward slashes are normalized", ad.is_gate_exempt("D:/QMT交易端模拟"))
check("G3 trailing separator is dropped", ad.is_gate_exempt(S + "\\"))
check("G4 trailing forward separator", ad.is_gate_exempt("D:/QMT交易端模拟/"))
check("G5 case-insensitive (drive letter)", ad.is_gate_exempt("d:\\QMT交易端模拟"))

check("G6 主号 is NOT exempt", not ad.is_gate_exempt(r"D:\国金QMT交易端模拟"))
check("G7 交易版 is NOT exempt", not ad.is_gate_exempt(r"D:\国金证券QMT交易端"))
check("G8 a subdir must NOT match", not ad.is_gate_exempt(S + r"\python"))
check("G9 prefix-eater (X2) must NOT match", not ad.is_gate_exempt(r"D:\QMT交易端模拟X"))
check("G10 empty / None must not crash", not ad.is_gate_exempt("") and not ad.is_gate_exempt(None))

# 时段窗口本身的行为没变（只有「谁被拦」变了）
import datetime as dt
t_in = dt.datetime(2026, 10, 29, 10, 0).timetuple()
t_out = dt.datetime(2026, 10, 29, 20, 0).timetuple()
check("G11 10:00 is inside the block window", ad.in_block_window(t_in))
check("G12 20:00 is outside", not ad.in_block_window(t_out))
check("G13 production sandbox constant unchanged", ad.SANDBOX_DIR == S)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS (%d checks)" % 13)
sys.exit(0)
