# coding: utf-8
"""QMT 夜间闭环（Nightly Loop）— 构建 → 部署 → 重启 → 核对 → 体检 → 报告。

它把 qmt_autodeploy.py 的「一次部署」升级成「每天自动跑一轮的闭环」：
  1) 重新构建产物（可选 --build）
  2) 落盘 + 重启指定客户端 + 等 BUILD_TAG（复用 qmt_autodeploy，含全部护栏）
  3) 扫描客户端日志（可多客户端，只读），输出每个模型的 BUILD_TAG / 被动态 / 心跳 / 异常
  4) 落报告 md + json，供人看、供 agent 接手诊断

设计边界（2026-10-28）：
  * 只重启 --qmt-dir 指定的那一个客户端，绝不碰另一台（qmt_autodeploy 按 ExecutablePath 过滤）；
  * 扫描任意客户端目录都只读，不写、不重启；
  * 构建失败 = 不部署（fail-safe），只报告；
  * 「自动改代码」不在本脚本范围内——那是 agent 的活。本脚本负责把「哪里坏了、
    坏成什么样、原始日志在哪」整理成一份确定的结论，agent 读完直接改。

用法:
  # 只体检（只读，两个客户端都扫，随时可跑）
  python qmt_nightly.py scan --qmt-dir "D:\\国金QMT交易端模拟" --qmt-dir "D:\\QMT交易端模拟"

  # 完整闭环：重建 G2 → 部署到 70180771 沙箱 → 重启 → 核对 → 体检 → 报告
  python qmt_nightly.py loop \
      --build build_p16_g2_selfcontained.py \
      --product build/strategy_p16_g2_selfcontained.py \
      --model STRATEGY_P16_G2_SELFCONTAINED \
      --qmt-dir "D:\\QMT交易端模拟"

退出码:
  0  OK（部署 PASS 且无严重项）
  2  时段闸拒绝（交易时段，qmt_autodeploy 返回）
  3  构建失败 / 产物非法
  4  部署后未出 BUILD_TAG
  5  客户端重启失败
  6  环境错误
  10 部署正常但体检发现严重项（需 agent/人工接手）
"""
from __future__ import print_function

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AUTODEPLOY = os.path.join(HERE, "qmt_autodeploy.py")
REPORT_DIR = os.path.join(HERE, "results", "qmt_nightly")
DEFAULT_QMT = r"D:\QMT交易端模拟"
PROD_QMT = r"D:\国金QMT交易端模拟"

REC_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d+) \[")
MODEL_RE = re.compile(r"reqid\s*=\s*0(.+?)\\python\\([^\\]+?)\.py_(\d+)")
TAG_RE = re.compile(r"BUILD_TAG=([0-9A-Za-z._\-]+)")
PASSIVE_RE = re.compile(r"passive=(True|False)")
COUNT_RE = re.compile(r"pending=(-?\d+)\s+positions=(-?\d+)")

# 日志签名分级：
#   critical  = 必须处理（沙箱拦截 / 模型未注册 / 非被动态崩溃）
#   attention = 看一眼（被动态转被动、账本载入失败——可能是设计内行为）
#   noise     = 每次重启都有的正常噪声，仅统计不入判定
SIGS = [
    ("configFormula not exist", "critical", "模型名未在客户端注册"),
    ("Foribdden", "critical", "沙箱文件 IO 拦截"),
    ("PermissionError", "critical", "权限异常"),
    ("INIT-SNAP-ERR", "attention", "L3 快照/账本载入失败"),
    ("INIT-REBUILD-ERR", "attention", "账本重建失败"),
    ("Traceback", "critical", "异常栈"),
    ("auto run timeout", "noise", "客户端重启正常噪声"),
    ("parse error", "noise", "loadFile 解析噪声"),
]
PASSIVE_MARKERS = ("转被动", "passive=True")


def log(msg):
    print("[NIGHTLY] " + msg)
    sys.stdout.flush()


def decode(raw):
    """FormulaOutput 是 UTF-8，XtClient_*.log 多为 GBK，都兼容一下。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("gbk", "replace")


# ------------------------------------------------------------------
# 日志解析
# ------------------------------------------------------------------
def formula_log_path(qmt_dir, date_str):
    return os.path.join(qmt_dir, "userdata", "log",
                        "XtClient_FormulaOutput_%s.log" % date_str)


def parse_log(path, since=None):
    """返回 {model: records}；record = {ts, text}。非策略输出（客户端噪声）不计。
    since='YYYY-MM-DD HH:MM:SS' 时只保留该时刻之后的记录（部署后复检用）。"""
    models = {}
    if not os.path.exists(path):
        return models
    text = decode(open(path, "rb").read())
    cur = None
    for line in text.splitlines():
        if REC_RE.match(line):
            cur = None
            if since and line[:19] < since:
                continue
            m = MODEL_RE.search(line)
            if m:
                cur = {"ts": line[:19], "text": line}
                models.setdefault(m.group(2), []).append(cur)
        elif cur is not None:
            cur["text"] += "\n" + line
    return models


def classify(rec_text):
    """返回 (critical[], attention[], noise{})。被动态下的 Traceback 降级为 attention。"""
    passive = any(mk in rec_text for mk in PASSIVE_MARKERS)
    crit, att, noise = [], [], {}
    for sig, sev, desc in SIGS:
        if sig not in rec_text:
            continue
        if sev == "noise":
            noise[sig] = noise.get(sig, 0) + 1
            continue
        if sig == "Traceback" and passive:
            sev = "attention"
        (crit if sev == "critical" else att).append((sig, desc))
    return crit, att, noise


def summarize_model(records):
    info = {
        "records": len(records),
        "first_ts": records[0]["ts"] if records else None,
        "last_ts": records[-1]["ts"] if records else None,
        "build_tag": None,
        "passive": None,
        "pending": None,
        "positions": None,
        "critical": [],
        "attention": [],
        "noise": {},
        "samples": [],
    }
    for rec in records:
        t = rec["text"]
        m = TAG_RE.search(t)
        if m:
            info["build_tag"] = m.group(1)
        for m in PASSIVE_RE.finditer(t):
            info["passive"] = m.group(1) == "True"
        m = COUNT_RE.search(t)
        if m:
            info["pending"] = int(m.group(1))
            info["positions"] = int(m.group(2))
        crit, att, noise = classify(t)
        for sig, desc in crit:
            if sig not in [x[0] for x in info["critical"]]:
                info["critical"].append((sig, desc))
                if len(info["samples"]) < 6:
                    info["samples"].append(rec["ts"] + " | " + sig + " | " +
                                           t.replace("\n", " ")[:300])
        for sig, desc in att:
            if sig not in [x[0] for x in info["attention"]]:
                info["attention"].append((sig, desc))
        for k, v in noise.items():
            info["noise"][k] = info["noise"].get(k, 0) + v
    return info


def scan_client(qmt_dir, date_str, expect_models=(), since=None):
    path = formula_log_path(qmt_dir, date_str)
    models = parse_log(path, since=since)
    report = {
        "qmt_dir": qmt_dir,
        "log": path,
        "log_exists": os.path.exists(path),
        "log_size": os.path.getsize(path) if os.path.exists(path) else 0,
        "since": since,
        "models": {},
        "critical": [],
        "attention": [],
        "missing": [],
    }
    for model, recs in sorted(models.items()):
        info = summarize_model(recs)
        report["models"][model] = info
        if info["critical"]:
            report["critical"].append(model)
        if info["attention"]:
            report["attention"].append(model)
    for m in expect_models:
        if m not in models:
            report["missing"].append(m)
            report["critical"].append(m)
    return report


def verdict_of(scans):
    crit = any(s["critical"] for s in scans)
    att = any(s["attention"] for s in scans)
    if crit:
        return "FAIL"
    return "ATTENTION" if att else "OK"


# ------------------------------------------------------------------
# 报告
# ------------------------------------------------------------------
def fmt_model_row(model, info):
    flag = "OK"
    if info["critical"]:
        flag = "FAIL"
    elif info["attention"]:
        flag = "注意"
    passive = "-" if info["passive"] is None else ("是" if info["passive"] else "否")
    return "| %s | %d | %s | %s | %s | %s/%s | %s |" % (
        model, info["records"], (info["last_ts"] or "-"),
        (info["build_tag"] or "-"), passive,
        ("-" if info["pending"] is None else info["pending"]),
        ("-" if info["positions"] is None else info["positions"]),
        flag)


def render_md(stamp, deploy, scans, verdict):
    L = []
    L.append("# QMT 夜间闭环报告 · %s" % stamp)
    L.append("")
    L.append("## 一、结论")
    L.append("")
    L.append("- **总判定：%s**" % verdict)
    if deploy:
        L.append("- **部署：%s**（模型 `%s`，BUILD_TAG `%s`，退出码 %s）"
                 % (deploy.get("result"), deploy.get("model"),
                    deploy.get("tag") or "-", deploy.get("rc")))
        if deploy.get("detail"):
            L.append("- 部署细节：%s" % deploy["detail"])
    else:
        L.append("- 部署：本次未执行（只体检）")
    L.append("")
    L.append("## 二、客户端扫描")
    for s in scans:
        L.append("")
        L.append("### %s" % s["qmt_dir"])
        L.append("")
        L.append("日志：`%s`（%s 字节，%s）"
                 % (s["log"], s["log_size"], "存在" if s["log_exists"] else "**不存在**"))
        if s.get("since"):
            L.append("")
            L.append("> 只统计 `%s` 之后的记录（部署前记录不计）。" % s["since"])
        L.append("")
        if not s["models"]:
            L.append("> 当日日志内没有任何策略输出。")
            continue
        L.append("| 模型 | 记录数 | 最后输出 | BUILD_TAG | 被动 | pending/positions | 判定 |")
        L.append("| --- | --- | --- | --- | --- | --- | --- |")
        for model, info in sorted(s["models"].items()):
            L.append(fmt_model_row(model, info))
        for m in s["missing"]:
            L.append("| %s | 0 | - | - | - | - | **FAIL（当日无输出）** |" % m)
    L.append("")
    L.append("## 三、发现明细")
    L.append("")
    any_finding = False
    for s in scans:
        for model, info in sorted(s["models"].items()):
            for sig, desc in info["critical"]:
                any_finding = True
                L.append("- **[严重]** `%s` / %s %s —— %s" % (s["qmt_dir"], model, sig, desc))
            for sig, desc in info["attention"]:
                any_finding = True
                L.append("- [关注] `%s` / %s %s —— %s" % (s["qmt_dir"], model, sig, desc))
        for m in s["missing"]:
            any_finding = True
            L.append("- **[严重]** `%s` / %s 当日无任何输出（未加载？被跳过？）" % (s["qmt_dir"], m))
    if not any_finding:
        L.append("无。")
    L.append("")
    L.append("## 四、日志样本（严重项前几条）")
    L.append("")
    has_sample = False
    for s in scans:
        for model, info in sorted(s["models"].items()):
            for smp in info["samples"]:
                has_sample = True
                L.append("- `%s` / %s：`%s`" % (s["qmt_dir"], model, smp.replace("|", "\\|")))
    if not has_sample:
        L.append("无。")
    L.append("")
    L.append("## 五、下一步（给 agent / 人）")
    L.append("")
    if verdict == "OK":
        L.append("无需动作。链路健康，等待下一次夜间闭环。")
    elif verdict == "ATTENTION":
        L.append("存在「关注」级发现，多为设计内被动（白名单空 / 账号戳不匹配）。")
        L.append("确认被动原因仍成立即可；若本应活跃而处于被动，按 `INIT-*` 行的原因排查。")
    else:
        L.append("存在「严重」级发现，按顺序处理：")
        L.append("1. 读 `第三节` 定位签名，再到 `userdata/log/XtClient_FormulaOutput_<date>.log` 取完整栈；")
        L.append("2. 改源码 → `python build_p16_g2_selfcontained.py` → `python qmt_autodeploy.py ...`；")
        L.append("3. 重跑 `python qmt_nightly.py scan` 确认严重项清零。")
    L.append("")
    return "\n".join(L)


def write_report(stamp, deploy, scans, verdict):
    if not os.path.isdir(REPORT_DIR):
        os.makedirs(REPORT_DIR)
    base = os.path.join(REPORT_DIR, stamp)
    payload = {
        "stamp": stamp,
        "verdict": verdict,
        "deploy": deploy,
        "scans": scans,
    }
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    md = render_md(stamp, deploy, scans, verdict)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(REPORT_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return base + ".md", base + ".json"


# ------------------------------------------------------------------
# 子命令
# ------------------------------------------------------------------
def cmd_scan(args):
    date_str = args.date or time.strftime("%Y%m%d")
    scans = [scan_client(d, date_str, args.expect_model or ()) for d in args.qmt_dir]
    verdict = verdict_of(scans)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    md, js = write_report(stamp, None, scans, verdict)
    log("扫描完成：%s" % verdict)
    for s in scans:
        log("  %s -> %d 个模型，严重 %s" %
            (s["qmt_dir"], len(s["models"]), s["critical"] or "无"))
    log("报告: %s" % md)
    print(md)
    return 0 if verdict != "FAIL" else 10


def run_autodeploy(product, model, qmt_dir, build=None, timeout=300, extra=()):
    cmd = [sys.executable, AUTODEPLOY, "--product", product, "--model", model,
           "--qmt-dir", qmt_dir, "--timeout", str(timeout)]
    if build:
        cmd[2:2] = ["--build", build]
    cmd.extend(extra)
    log("调用 qmt_autodeploy: %s" % " ".join(cmd[2:]))
    p = subprocess.run(cmd, cwd=HERE, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT)
    out = decode(p.stdout)
    for line in out.splitlines():
        print("  " + line)
    sys.stdout.flush()
    tag = None
    m = re.search(r"BUILD_TAG=([0-9A-Za-z._\-]+)", out)
    if m:
        tag = m.group(1)
    return p.returncode, out, tag


def cmd_loop(args):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    product = args.product if os.path.isabs(args.product) else os.path.join(HERE, args.product)
    build = args.build
    if build and not os.path.isabs(build):
        build = os.path.join(HERE, build)

    deploy = {"model": args.model, "rc": None, "result": "未执行", "tag": None, "detail": ""}
    # 部署目标侧只看「本次部署之后」的记录：否则白天累积的旧告警会让每次部署都判 ATTENTION/FAIL
    deploy_start = time.strftime("%Y-%m-%d %H:%M:%S")
    extra = []
    if args.force_offhours:
        extra.append("--force-offhours")
    if args.no_restart:
        extra.append("--no-restart")
    if args.deploy_dry_run:
        extra.append("--dry-run")
    rc, out, tag = run_autodeploy(product, args.model, args.qmt_dir, build,
                                  timeout=args.timeout, extra=extra)
    deploy["rc"] = rc
    deploy["tag"] = tag

    # 演练模式（不重启）不做「必须出现该模型」的期望，否则会把演练误判成 FAIL
    rehearsal = args.no_restart or args.deploy_dry_run
    scans = [scan_client(d, time.strftime("%Y%m%d"),
                         (args.model,) if (d == args.qmt_dir and not rehearsal) else (),
                         since=(deploy_start if d == args.qmt_dir else None))
             for d in args.scan_qmt_dir]

    if rc == 0:
        deploy["result"] = "PASS"
    elif rc == 2:
        deploy["result"] = "拒绝（交易时段）"
    elif rc == 3:
        deploy["result"] = "FAIL（构建/产物）"
    elif rc == 4:
        deploy["result"] = "FAIL（未出 BUILD_TAG）"
    elif rc == 5:
        deploy["result"] = "FAIL（重启失败）"
    else:
        deploy["result"] = "FAIL（rc=%d）" % rc

    if rc == 0:
        if args.no_restart or args.deploy_dry_run:
            deploy["detail"] = ("本机未重启（%s），跳过「日志 TAG 生效」核对；"
                                "产品校验通过，TAG=%s"
                                % ("--no-restart" if args.no_restart else "--dry-run", tag))
        else:
            for s in scans:
                if s["qmt_dir"] == args.qmt_dir:
                    info = s["models"].get(args.model)
                    if not info or info["build_tag"] != tag:
                        deploy["detail"] = ("部署后日志中的 BUILD_TAG 与本次产物不一致"
                                            "（部署 TAG=%s，日志 TAG=%s）"
                                            % (tag, info["build_tag"] if info else "无输出"))
                        deploy["result"] = "FAIL（TAG 未生效）"
                        rc = 4
                    else:
                        deploy["detail"] = ("日志确认 %s BUILD_TAG=%s，passive=%s，"
                                            "pending=%s positions=%s"
                                            % (args.model, info["build_tag"], info["passive"],
                                               info["pending"], info["positions"]))
    else:
        deploy["detail"] = out.strip().splitlines()[-1][:400] if out.strip() else ""

    verdict = verdict_of(scans)
    md, js = write_report(stamp, deploy, scans, verdict)
    log("部署: %s | 体检: %s" % (deploy["result"], verdict))
    log("报告: %s" % md)

    if args.alert and (rc != 0 or verdict == "FAIL"):
        try:
            sys.path.insert(0, HERE)
            from qmt_bridge_client import notify_feishu
            msg = ("[QMT 夜间闭环] 部署=%s 体检=%s\n%s\n报告: %s"
                   % (deploy["result"], verdict, deploy.get("detail", ""), md))
            ok = notify_feishu(msg)
            log("飞书告警: %s" % ("已发送" if ok else "发送失败"))
        except Exception as e:
            log("[WARN] 飞书告警异常（不影响主流程）: %s" % e)

    if rc != 0:
        return rc
    return 0 if verdict != "FAIL" else 10


def main():
    ap = argparse.ArgumentParser(description="QMT 夜间闭环（部署 + 核对 + 体检）")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("scan", help="只读体检（不部署不重启）")
    sp.add_argument("--qmt-dir", action="append", default=None,
                    help="要扫描的客户端目录，可重复；缺省扫生产+沙箱两台")
    sp.add_argument("--date", default=None, help="日志日期 YYYYMMDD，默认今天")
    sp.add_argument("--expect-model", action="append", default=None,
                    help="必须出现的模型名，缺失判严重（可重复）")
    sp.set_defaults(func=cmd_scan)

    lp = sub.add_parser("loop", help="构建→部署→重启→核对→体检→报告")
    lp.add_argument("--product", required=True, help="构建产物路径")
    lp.add_argument("--model", required=True, help="QMT 模型名")
    lp.add_argument("--qmt-dir", required=True, help="要部署并重启的客户端目录")
    lp.add_argument("--build", default=None, help="可选：先执行的构建脚本")
    lp.add_argument("--timeout", type=int, default=300, help="等 BUILD_TAG 秒数")
    lp.add_argument("--scan-qmt-dir", action="append", default=None,
                    help="体检扫描的客户端目录（可重复）；缺省=部署目标 + 生产端只读")
    lp.add_argument("--force-offhours", action="store_true", help="越过交易时段闸（危险）")
    lp.add_argument("--no-restart", action="store_true",
                    help="只落盘不重启（跳过 TAG 生效核对，供白天演练）")
    lp.add_argument("--deploy-dry-run", action="store_true",
                    help="只做产物校验不落盘不重启（供白天演练）")
    lp.add_argument("--alert", action="store_true", help="失败时推飞书")
    lp.set_defaults(func=cmd_loop)

    args = ap.parse_args()
    if not getattr(args, "func", None):
        ap.print_help()
        return 6
    if args.cmd == "scan" and not args.qmt_dir:
        args.qmt_dir = [PROD_QMT, DEFAULT_QMT]
    if args.cmd == "loop" and not args.scan_qmt_dir:
        args.scan_qmt_dir = [args.qmt_dir, PROD_QMT]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main() or 0)
