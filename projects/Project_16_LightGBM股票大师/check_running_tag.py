# coding: utf-8
"""执行壳「运行态 TAG 核对」——盘上产物 vs 日志里正在跑的那份（2026-10-30 立）。

**为什么需要**：`--no-restart` 落盘会把新产物写进 `<QMT>\\python\\` 但**不加载**，
于是出现「盘上 TAG = 新、运行中 TAG = 旧」的失真态。此时任何「盘上产物已部署」的
结论都不成立——2026-10-30 审计就是把盘上 TAG（20261030-101114）当成了运行证据，
而日志 INIT 行实际是 20261029-195335 / 20261030-094150，策略当天根本没加载新版。

**判据（只认日志 INIT 行，不认文件 mtime、不认 BUILD_TAG 出现在文件里）**：
  1. 读 `<QMT>\\python\\<模型>.py` 的 BUILD_TAG（盘上）；
  2. 读当天 FormulaOutput 里**属于该模型**的最后一条 `INIT BUILD_TAG=`（运行中）；
  3. 两者不一致 ⇒ `STALE`（盘上比运行中新）；运行中没有 INIT ⇒ `NOT_RUNNING`。

**为什么要按模型名过滤**：FormulaOutput 是各策略混写的一个文件，
`STRATEGY_P16_G2_EXEC` 的 INIT 行和 `STRATEGY_P16_G2_EXEC_SBX` 的会长得像，
必须用「模型名.py」精确匹配（`.py` 收尾防父串吃掉子串）。

退出码：0 全部一致 / 1 存在 STALE 或 NOT_RUNNING / 3 路径被拒（交易版）。
"""
from __future__ import print_function

import io
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FORBIDDEN_DIR = r"D:\国金证券QMT交易端"

# 模型名 -> 客户端目录
TARGETS = [
    ("STRATEGY_P16_G2_EXEC", r"D:\国金QMT交易端模拟"),
    ("STRATEGY_P16_V13_EXEC", r"D:\国金QMT交易端模拟"),
    ("STRATEGY_P16_G2_EXEC_SBX", r"D:\QMT交易端模拟"),
]

TAG_RE = re.compile(r'BUILD_TAG\s*=\s*"([0-9A-Za-z._\-]+)"')
INIT_TAG_RE = re.compile(r"INIT BUILD_TAG=([0-9A-Za-z._\-]+)")


def _norm(p):
    return os.path.normcase(os.path.normpath(p or "")).rstrip("\\/")


def _is_forbidden(path):
    return _norm(FORBIDDEN_DIR) in _norm(path)


def disk_tag(qmt_dir, model):
    """盘上产物的 BUILD_TAG；文件缺失/读不到返回 (None, 原因)。"""
    p = os.path.join(qmt_dir, "python", "%s.py" % model)
    if not os.path.exists(p):
        return None, "盘上产物不存在: %s" % p
    try:
        raw = open(p, "rb").read()
    except Exception as e:
        return None, "读取失败: %s" % str(e)[:60]
    txt = raw.decode("gbk", "replace")
    m = TAG_RE.search(txt)
    if not m:
        return None, "盘上产物无 BUILD_TAG 字面量（可能是加密形态）"
    return m.group(1), ""


def _model_lines(txt, model):
    key = "%s.py" % model
    return [ln for ln in txt.splitlines() if key in ln]


def running_tag(qmt_dir, model, date_str=None):
    """当天日志里该模型最后一条 INIT 的 BUILD_TAG。返回 (tag, 原因)。"""
    date_str = date_str or time.strftime("%Y%m%d")
    p = os.path.join(qmt_dir, "userdata", "log", "XtClient_FormulaOutput_%s.log" % date_str)
    if not os.path.exists(p):
        return None, "当天日志不存在: %s" % p
    try:
        txt = open(p, "rb").read().decode("utf-8", "replace")
    except Exception as e:
        return None, "日志读取失败: %s" % str(e)[:60]
    tags = []
    for ln in _model_lines(txt, model):
        m = INIT_TAG_RE.search(ln)
        if m:
            tags.append(m.group(1))
    if not tags:
        return None, "日志里没有该模型的 INIT（策略未启动/未加载）"
    return tags[-1], ""


def check_one(model, qmt_dir, date_str=None):
    """返回 dict：status ∈ OK / STALE / NOT_RUNNING / NO_DISK / FORBIDDEN。"""
    if _is_forbidden(qmt_dir):
        return {"model": model, "status": "FORBIDDEN", "note": "交易版路径一律不碰"}
    dt, dnote = disk_tag(qmt_dir, model)
    rt, rnote = running_tag(qmt_dir, model, date_str)
    if dt is None:
        return {"model": model, "status": "NO_DISK", "disk": None, "running": rt,
                "note": dnote}
    if rt is None:
        return {"model": model, "status": "NOT_RUNNING", "disk": dt, "running": None,
                "note": rnote}
    if dt != rt:
        return {"model": model, "status": "STALE", "disk": dt, "running": rt,
                "note": "盘上是 %s，日志里在跑的是 %s（--no-restart 落盘后未加载）" % (dt, rt)}
    return {"model": model, "status": "OK", "disk": dt, "running": rt, "note": ""}


def main(argv):
    date_str = None
    for a in argv[1:]:
        if a.startswith("--date="):
            date_str = a.split("=", 1)[1].strip()
    bad = 0
    print("[TAGCHECK] %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    for model, qmt_dir in TARGETS:
        r = check_one(model, qmt_dir, date_str)
        print("  [%-11s] %-26s disk=%-16s running=%-16s %s" % (
            r["status"], model, r.get("disk") or "-", r.get("running") or "-",
            r.get("note") or ""))
        if r["status"] in ("STALE", "NOT_RUNNING", "NO_DISK", "FORBIDDEN"):
            bad += 1
    if bad:
        print("[TAGCHECK] %d 项不一致：盘上产物不等于正在运行的代码" % bad)
        return 1
    print("[TAGCHECK] 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
