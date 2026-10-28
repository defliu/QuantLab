# -*- coding: utf-8 -*-
"""QMT 客户端登录状态探针（确定性脚本，零 LLM 依赖）

用途：夜间闭环 `Quant_P16_QMT_NightlyDeploy` 的失败绝大多数不是「产物/构建」问题，
而是客户端根本没登上券商柜台——策略压根不会被加载，于是轮询 BUILD_TAG 300s 超时，
报告只写「当日无任何输出」，看不出根因。本脚本把「登没上、为什么没登上」一次说清。

判据（全部来自本机实测日志签名，2026-10-29 校准）：
  登录成功  `[LOGIN][RESP] account [...] login success`
            `[AutoRunStrategy]account:70180771 login success`
  连接建立  `[TC::CProxyClient::onConnected] ... remote_addr = <ip>:<port>`（TCP 层已通）
  应用层超时 `onLogin ... ErrorID: 200003, ErrorMsg: "超时"`（TCP 通、柜台不应答 = 券商侧）
  端口拒连  `login status: 1 description: 由于目标计算机积极拒绝，无法连接。`（TCP 层就被拒）
  引擎等待  `[LOGIN] Proxy client not logged`（策略引擎空转，没账户）

安全边界（硬编码，不接受参数放宽）：
  只读日志 + 只探 TCP；绝不启动/重启/杀任何进程；
  目录命中 `国金证券QMT交易端`（真实交易账号）直接拒绝并报错退出。

用法：
  python scripts/qmt_login_probe.py                      # 主号 + 沙箱
  python scripts/qmt_login_probe.py --json               # 机器可读
  python scripts/qmt_login_probe.py --client "D:\\QMT交易端模拟"
退出码：0=全部已登录  2=有客户端未登录  4=进程不在  5=参数/路径违规
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import socket
import sys

FORBIDDEN_MARK = "国金证券QMT交易端"  # 真实交易客户端，禁止任何读写/扫描
DEFAULT_CLIENTS = [r"D:\国金QMT交易端模拟", r"D:\QMT交易端模拟"]
DEFAULT_NODE = ("180.169.107.19", 56001)

RE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[,.]\d+")
RE_REMOTE = re.compile(r"remote_addr = (\d+\.\d+\.\d+\.\d+):(\d+)")
SIGN_SUCCESS = ("login success",)
SIGN_TIMEOUT_200003 = ("200003",)
SIGN_REFUSE = ("积极拒绝",)
SIGN_WAIT_ENGINE = ("Proxy client not logged",)


def read_tail(path, max_bytes=600_000, whole=False):
    """读日志尾部，返回按行文本列表。

    QMT 引擎日志是**混合编码**（英文行 ASCII、部分中文行 UTF-8、部分中文行 GBK），
    整体按一种编码解会把另一类中文解成乱码（实测 `错误200003,超时` 按 GBK 解出 `閿欒...`），
    故逐行先试 UTF-8、失败退回 GBK。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if not whole:
        max_bytes = min(max_bytes, size)
    with open(path, "rb") as f:
        if not whole and size > max_bytes:
            f.seek(size - max_bytes)
        raw = f.read()
    lines = []
    for chunk in raw.split(b"\n"):
        if not chunk.strip():
            continue
        try:
            lines.append(chunk.decode("utf-8"))
        except UnicodeDecodeError:
            lines.append(chunk.decode("gbk", "replace"))
    return lines


def parse_ts(line):
    m = RE_TS.match(line)
    if not m:
        return None
    try:
        return dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def tcp_ok(host, port, timeout=3.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def find_process_start(dir_path):
    """用 WMI 查该目录下 XtItClient.exe 的启动时间（只读查询，不碰进程）。"""
    ps = (
        "$d='%s'; $r=@(); Get-CimInstance Win32_Process -Filter \"Name='XtItClient.exe'\" | "
        "ForEach-Object { if ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($d)) { "
        "$r += ('{0}|{1}' -f $_.ProcessId, $_.CreationDate) } }; $r -join ';'" % dir_path.replace("'", "''")
    )
    import subprocess

    try:
        out = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps],
            capture_output=True, timeout=30,
        ).stdout.decode("gbk", "replace")
    except Exception as exc:  # noqa: BLE001
        return None, None, "WMI 查询失败: %s" % exc
    pid, start = None, None
    for chunk in out.strip().split(";"):
        if "|" not in chunk:
            continue
        a, b = chunk.split("|", 1)
        pid = int(a)
        try:
            start = dt.datetime.strptime(b.strip()[:19], "%Y%m%d%H%M%S")
        except ValueError:
            try:  # 某些区域格式：2026/10/29 0:23:09
                start = dt.datetime.strptime(b.strip()[:19], "%Y/%m/%d %H:%M:%S")
            except ValueError:
                start = None
        break
    return pid, start, None


def probe_client(dir_path, date_str=None, as_of=None):
    res = {
        "client": dir_path,
        "process_pid": None,
        "process_start": None,
        "logged_in": False,
        "state": "未知",
        "login_node": None,
        "tcp_node_ok": None,
        "last_success": None,
        "last_failure": None,
        "detail": "",
    }
    if FORBIDDEN_MARK in dir_path:
        res["state"] = "禁止扫描真实交易客户端"
        res["detail"] = "命中 %s" % FORBIDDEN_MARK
        return res, 5

    live = as_of is None
    pid, start, err = find_process_start(dir_path) if live else (None, None, None)
    res["process_pid"] = pid
    res["process_start"] = start.strftime("%Y-%m-%d %H:%M:%S") if start else None
    if err:
        res["detail"] = err

    if date_str is None:
        date_str = dt.date.today().strftime("%Y%m%d")
    res["log_date"] = date_str
    main_log = os.path.join(dir_path, "userdata", "log", "XtClient_%s.log" % date_str)
    res["main_log"] = main_log
    all_lines = read_tail(main_log, whole=not live)
    lines = [l for l in all_lines if "[LOGIN]" in l or "CProxyClient" in l or "CLoginDialog" in l]
    cutoff = None
    if as_of is not None:
        day = dt.datetime.strptime(date_str, "%Y%m%d")
        cutoff = day + dt.timedelta(hours=int(as_of[:2]), minutes=int(as_of[2:4]), seconds=int(as_of[4:6]))
        lines = [l for l in lines if (parse_ts(l) or dt.datetime.max) <= cutoff]
        res["as_of"] = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    node = None
    for l in reversed(lines):
        m = RE_REMOTE.search(l)
        if m:
            node = (m.group(1), int(m.group(2)))
            break
    if node is None:
        node = DEFAULT_NODE
    res["login_node"] = "%s:%d" % node
    # 历史回放不探活 TCP（当下的连通性不代表当时）
    res["tcp_node_ok"] = tcp_ok(*node) if live else None

    last_ok = last_timeout = last_refuse = last_engine_wait = None
    for l in lines:
        ts = parse_ts(l)
        if any(s in l for s in SIGN_SUCCESS):
            last_ok = (ts, l)
        elif any(s in l for s in SIGN_TIMEOUT_200003):
            last_timeout = (ts, l)
        elif any(s in l for s in SIGN_REFUSE):
            last_refuse = (ts, l)
        elif any(s in l for s in SIGN_WAIT_ENGINE):
            last_engine_wait = (ts, l)

    # `Proxy client not logged` 每 5 秒刷一条，永远是"最新一行"，会盖掉真正的登录尝试结论，
    # 因此它不参与排序，只在三种登录事件全部缺席时兜底。
    cands = [("login success", last_ok), ("200003 超时", last_timeout), ("端口拒连", last_refuse)]
    cands = [(k, v) for k, v in cands if v]
    cands.sort(key=lambda kv: kv[1][0] or dt.datetime.min)
    if cands:
        kind, (ts, line) = cands[-1]
        res["last_failure"] = ts.strftime("%H:%M:%S") if ts else None
        if kind == "login success":
            res["logged_in"] = True
            res["state"] = "已登录"
            res["last_success"] = ts.strftime("%Y-%m-%d %H:%M:%S") if ts else None
        elif kind == "200003 超时":
            res["state"] = "未登录：TCP 已通、柜台登录报文 120s 不应答（券商侧，非本机；人工点登录同样超时）"
        else:
            res["state"] = "未登录：登录节点拒绝 TCP（券商侧断连）"
        res["detail"] = (line or "")[-180:]
    elif last_engine_wait:
        res["state"] = "未登录：策略引擎报 Proxy client not logged（当日无登录尝试记录）"
        res["detail"] = last_engine_wait[1][-180:]

    # 实时判定时，本轮进程启动之前的 login success 属于上一轮，不能算已登录
    # （夜间闭环每轮都会重启客户端，重启后到登录成功之间有 30~90s 窗口）。
    if live and res["logged_in"] and start is not None and last_ok and last_ok[0] and last_ok[0] < start:
        res["logged_in"] = False
        res["state"] = "未登录：本轮进程启动（%s）之后尚无 login success" % start.strftime("%H:%M:%S")

    if live and pid is None and not res["logged_in"]:
        res["state"] = "客户端进程不在（未启动）"
        return res, 4

    if res["logged_in"]:
        # 登录后策略是否真被加载（FormulaOutput 有新增行）
        fo = glob.glob(os.path.join(dir_path, "userdata", "log", "XtClient_FormulaOutput_%s.log" % date_str))
        fresh = False
        if fo:
            try:
                ref = dt.datetime.now() if live else cutoff
                fresh = (ref - dt.datetime.fromtimestamp(os.path.getmtime(fo[0]))).total_seconds() < 3 * 3600
            except OSError:
                fresh = False
        res["formula_output_fresh"] = fresh
        return res, 0
    return res, 2


def main():
    ap = argparse.ArgumentParser(description="QMT 客户端登录状态探针（只读）")
    ap.add_argument("--client", action="append", default=None,
                    help="客户端目录，可多次；默认主号 + 沙箱")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verdict", action="store_true",
                    help="输出 shell 可解析的单行键值（LOGGED=0/1 RC=n EXIT=n），"
                         "给定时任务旁路调用，避免 PowerShell 受控制台编码影响解不了 JSON")
    ap.add_argument("--date", default=None, help="回放指定日期日志 YYYYMMDD")
    ap.add_argument("--as-of", default=None,
                    help="回放到该时刻 HHMMSS（只统计此刻之前的登录事件；历史复盘用，不探活 TCP/进程）")
    args = ap.parse_args()

    clients = args.client or DEFAULT_CLIENTS
    rc_all = 0
    results = []
    rcs = []
    for c in clients:
        r, rc = probe_client(c, date_str=args.date, as_of=args.as_of)
        results.append(r)
        rcs.append(rc)
        rc_all = rc_all if rc == 0 else rc
    if args.verdict:
        # 单客户端旁路调用（定时任务用）：只输出可正则的稳定键值，状态原文附在后面
        r = results[-1]
        print("LOGGED=%d EXIT=%d NODE=%s TCP=%s STATE=%s" % (
            1 if r["logged_in"] else 0, rcs[-1], r["login_node"],
            "1" if r.get("tcp_node_ok") else "0", r["state"]))
        return rc_all
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for r in results:
            print("[%s] %s pid=%s 节点=%s TCP=%s 状态=%s" % (
                "OK " if r["logged_in"] else "DOWN", r["client"], r["process_pid"],
                r["login_node"], r["tcp_node_ok"], r["state"]))
            if r["detail"]:
                print("      last: %s" % r["detail"])
    return rc_all


if __name__ == "__main__":
    sys.exit(main())
