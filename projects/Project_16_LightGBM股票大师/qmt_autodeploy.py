# coding: utf-8
"""QMT 零介入部署器（Zero-Touch Deploy）— P16 系列。

把「构建产物 -> QMT 客户端运行 -> 回读日志核对 BUILD_TAG」全链路脚本化，
取代人工「记事本打开 -> 客户端策略编辑器粘贴 -> 保存」的动作。

核心实证（2026-10-28，见 specs/QMT零介入部署方案_20261028.md）：
  1) <QMT>\\python\\<模型名>.py 下的**明文** GBK 单文件会被客户端直接加载执行；
  2) 客户端不会把明文文件重新加密或还原（正常跑完一轮、强杀进程后均不变）；
  3) 唯一前置条件：**模型名必须已在客户端注册过**（indexConfig/configFormula 内），
     仅靠改 indexUserConfig.xml 插入新名字无效（客户端日志 configFormula not exist 后静默跳过）。

用法:
  # 1) 只做构建 + 落盘（不重启客户端）
  python qmt_autodeploy.py --product build/strategy_p16_g2_selfcontained.py \
      --model STRATEGY_P16_G2_SELFCONTAINED --qmt-dir "D:\\QMT交易端模拟" --no-restart

  # 2) 构建产物 -> 部署 -> 重启 -> 等 BUILD_TAG（完整链路，仅非交易时段）
  python qmt_autodeploy.py --product build/strategy_p16_g2_selfcontained.py \
      --model STRATEGY_P16_G2_SELFCONTAINED --qmt-dir "D:\\QMT交易端模拟"

  # 3) 先跑项目构建脚本再部署
  python qmt_autodeploy.py --build build_p16_g2_selfcontained.py \
      --product build/strategy_p16_g2_selfcontained.py \
      --model STRATEGY_P16_G2_SELFCONTAINED --qmt-dir "D:\\QMT交易端模拟"

退出码:
  0  PASS（客户端已加载并打印期望 BUILD_TAG）
  2  时段闸拒绝（仅对非沙箱客户端；--force-offhours 可越过）
  3  产物校验失败（非 GBK / 首行非 # coding=gbk / 取不到 BUILD_TAG）
  4  部署后未看到 BUILD_TAG（含模型名未预注册的诊断）
  5  客户端重启失败（起不来 / 进程不在）
  6  运行环境错误（目录不存在等）
"""
from __future__ import print_function

import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_EXE_REL = os.path.join("bin.x64", "XtItClient.exe")

# 重启禁区：09:00-15:05 不许重启客户端（含午休，避免任何盘中抖动）。
# **只对「承载在跑策略、需要干净交易日证据」的客户端生效**（主号 67014907）。
# 沙箱 70180771（D:\QMT交易端模拟）是纯验证目标：策略永远 passive 空转、不出信号不建仓，
# 重启不产生任何需要保留的盘中证据，也没有真钱风险 ⇒ **免闸**，任何时间都可部署/重启
# （2026-10-29 诚哥拍板：主号与沙箱都是模拟账号，沙箱更没有理由锁死）。
BLOCK_START = (9, 0)
BLOCK_END = (15, 5)
SANDBOX_DIR = r"D:\QMT交易端模拟"

TAG_RE = re.compile(r"""BUILD_TAG\s*=\s*["']([0-9A-Za-z._\-]+)["']""")


def log(msg):
    print("[DEPLOY] " + msg)
    sys.stdout.flush()


def fail(code, msg):
    log("[FAIL] " + msg)
    sys.exit(code)


# ------------------------------------------------------------------
# 时段闸
# ------------------------------------------------------------------
def in_block_window(now=None):
    now = now or time.localtime()
    hm = (now.tm_hour, now.tm_min)
    return BLOCK_START <= hm < BLOCK_END


def is_gate_exempt(qmt_dir):
    """沙箱客户端免时段闸。

    路径必须先归一：调用方常传 D:/xxx 或带尾分隔符，原样比较会静默失配，把沙箱
    当成主号去拦（2026-10-29 「判定类脚本必须有第二证据」条的同类坑）。
    """
    if not qmt_dir:
        return False
    try:
        a = os.path.normcase(os.path.normpath(os.path.abspath(qmt_dir)))
        b = os.path.normcase(os.path.normpath(os.path.abspath(SANDBOX_DIR)))
    except Exception:
        return False
    return a == b


# ------------------------------------------------------------------
# PowerShell 辅助
# ------------------------------------------------------------------
def ps_run(script, timeout=60):
    """执行 PowerShell 片段，返回 (rc, text)。

    2026-10-28 修（中文路径变空列表）：子进程的 stdout 编码跟随父进程的
    [Console]::OutputEncoding。qmt_nightly_deploy.ps1 为写日志把控制台设成 UTF-8
    且同控制台里调用本脚本 → 这里的子 PowerShell 也跟着吐 UTF-8，旧代码一律按 GBK 解，
    中文路径（D:\\QMT交易端模拟）解成乱码 → startswith 比对失败 → list_client_pids()
    返回空（既杀不到也认不到，部署报「全程未见进程」而客户端其实起来了）。
    现在：①显式要求子进程用 UTF-8 输出；②解码先 utf-8 再 gbk 兜底。
    """
    wrapped = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;" + script
    p = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    raw = p.stdout
    for enc in ("utf-8", "gbk"):
        try:
            return p.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return p.returncode, raw.decode("gbk", "replace")


def list_client_pids(qmt_dir):
    qmt_dir = os.path.abspath(qmt_dir)
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-CimInstance Win32_Process -Filter \"Name='XtItClient.exe'\" | "
        "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.ExecutablePath }"
    )
    rc, out = ps_run(script)
    pids = []
    for line in out.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid_s, path = line.split("|", 1)
        if not pid_s.isdigit():
            continue
        # 只认本客户端目录下的进程，绝不误杀另一台客户端
        if os.path.normcase(os.path.abspath(path.strip())).startswith(os.path.normcase(qmt_dir + os.sep)):
            pids.append(int(pid_s))
    return pids


def kill_clients(qmt_dir, grace_sec=12):
    """优雅关闭优先（CloseMainWindow），超时再强杀。返回被杀 PID 列表。"""
    pids = list_client_pids(qmt_dir)
    if not pids:
        return []
    log("停止客户端 PID=%s" % ",".join(str(p) for p in pids))
    for pid in pids:
        ps_run("$p=Get-Process -Id %d -ErrorAction SilentlyContinue;"
               "if($p){$p.CloseMainWindow() | Out-Null}" % pid)
    deadline = time.time() + grace_sec
    while time.time() < deadline:
        time.sleep(1)
        if not list_client_pids(qmt_dir):
            log("客户端已优雅退出")
            return pids
    for pid in pids:
        ps_run("Stop-Process -Id %d -Force -ErrorAction SilentlyContinue" % pid)
    time.sleep(2)
    left = list_client_pids(qmt_dir)
    if left:
        log("[WARN] 强杀后仍有进程存活: %s" % left)
    else:
        log("客户端已强制退出")
    return pids


def start_client(qmt_dir, wait_sec=90, stable_sec=8, exclude=()):
    """启动客户端并等待其稳定运行。

    2026-10-28 修（假失败）：单实例交接会「先起一个进程→自查已存在实例→自杀→
    由存活实例/新进程接管登录」，启动初期 3 秒内进程可能根本不可见或 PID 会变。
    旧实现 sleep(3) 后只查一次 → 误报 fail(5)，而客户端实际已拉起（16:08 实证）。
    现在改为轮询：可见进程连续存在 stable_sec 秒才算成功，最长等 wait_sec 秒。
    """
    exe = os.path.join(qmt_dir, CLIENT_EXE_REL)
    if not os.path.exists(exe):
        fail(6, "客户端 exe 不存在: %s" % exe)
    # 必须带 'reboot' 参数：不带参数启动会停在「Proxy client not logged」，
    # 一个策略都不跑（2026-10-28 实证）。
    ps_run("Start-Process -FilePath '%s' -ArgumentList 'reboot'" % exe, timeout=30)
    exclude = set(int(p) for p in exclude)
    deadline = time.time() + wait_sec
    up_since = None
    last = []
    while time.time() < deadline:
        time.sleep(2)
        pids = [p for p in list_client_pids(qmt_dir) if p not in exclude]
        if not pids:
            if up_since is not None:
                log("[WARN] 客户端进程短暂消失（单实例交接），继续等待…")
            up_since = None
            continue
        last = pids
        if up_since is None:
            up_since = time.time()
            log("检测到客户端进程 PID=%s，等待稳定 %ds 后确认…"
                % (",".join(str(p) for p in pids), stable_sec))
            continue
        if time.time() - up_since >= stable_sec:
            log("客户端已稳定运行 PID=%s（reboot 模式）"
                % ",".join(str(p) for p in pids))
            return pids
    detail = ("最后可见 PID=%s" % last) if last else "全程未见进程"
    fail(5, "启动后 %ds 内客户端未稳定运行（%s）。请人工确认客户端是否起来。"
            % (wait_sec, detail))


# ------------------------------------------------------------------
# 产物校验
# ------------------------------------------------------------------
def validate_product(path):
    if not os.path.exists(path):
        fail(3, "产物不存在: %s" % path)
    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        fail(3, "产物带 UTF-8 BOM（QMT 不接受）")
    try:
        text = raw.decode("gbk")
    except Exception as e:
        fail(3, "产物不是合法 GBK: %s" % e)
    first = text.splitlines()[0].strip() if text else ""
    if first.replace(" ", "") not in ("#coding=gbk", "#coding:gbk"):
        fail(3, "产物首行不是 # coding=gbk，实际: %r" % first[:60])
    m = TAG_RE.search(text)
    if not m:
        fail(3, "产物内找不到 BUILD_TAG 字面量")
    return raw, m.group(1)


# ------------------------------------------------------------------
# 日志轮询
# ------------------------------------------------------------------
def read_from(path, offset):
    if not os.path.exists(path):
        return "", offset
    size = os.path.getsize(path)
    if offset > size:          # 日志被轮转/截断
        offset = 0
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    # 2026-10-28 修：XtClient_FormulaOutput_*.log 实际是 UTF-8（直接按 GBK 解会
    # 'gbk' codec can't decode byte 0xaf），XtClient_*.log 多为 GBK → 两种都试。
    for enc in ("utf-8", "gbk"):
        try:
            return chunk.decode(enc), offset + len(chunk)
        except Exception:
            continue
    return chunk.decode("gbk", "replace"), offset + len(chunk)


def log_paths(qmt_dir, date_str):
    base = os.path.join(qmt_dir, "userdata", "log")
    return (os.path.join(base, "XtClient_FormulaOutput_%s.log" % date_str),
            os.path.join(base, "XtClient_%s.log" % date_str))


def wait_for_tag(qmt_dir, model, tag, timeout_sec=300, interval=3):
    """轮询日志直到看到 BUILD_TAG=<tag>。返回 (ok, reason, excerpt)。"""
    formula_off = 0
    main_off = 0
    started = time.time()
    excerpt = []
    not_seeded_marker = "utfName:%s" % model

    # 起点：现有日志尾部（避免匹配到上一轮的旧 TAG）
    date_str = time.strftime("%Y%m%d")
    fo, mo = log_paths(qmt_dir, date_str)
    if os.path.exists(fo):
        formula_off = os.path.getsize(fo)
    if os.path.exists(mo):
        main_off = os.path.getsize(mo)
    log("开始轮询日志（起点 offset formula=%d main=%d），等待 BUILD_TAG=%s，超时 %ds"
        % (formula_off, main_off, tag, timeout_sec))

    while time.time() - started < timeout_sec:
        time.sleep(interval)
        date_str = time.strftime("%Y%m%d")
        fo, mo = log_paths(qmt_dir, date_str)
        txt, formula_off = read_from(fo, formula_off)
        if txt:
            for line in txt.splitlines():
                if "BUILD_TAG=%s" % tag in line or ("BUILD_TAG=" in line and tag in line):
                    excerpt.append(line.strip()[:300])
            if excerpt:
                return True, "matched", excerpt
            # 影子诊断：模型名未预注册（客户端 configFormula 里没有这个名字）
            if "configFormula not exist" in txt and model in txt:
                return False, "MODEL_NOT_SEEDED", [l.strip()[:300] for l in txt.splitlines() if model in l][-3:]
            # 保底：把关键行塞进 excerpt 供失败时展示
            for line in txt.splitlines():
                if "[INIT]" in line or "[BUILD]" in line or "Traceback" in line or "Error" in line:
                    excerpt.append(line.strip()[:300])
            excerpt = excerpt[-12:]
        mtxt, main_off = read_from(mo, main_off)
        if mtxt:
            if not_seeded_marker in mtxt and "configFormula not exist" in mtxt:
                return False, "MODEL_NOT_SEEDED", [l.strip()[:300] for l in mtxt.splitlines()
                                                   if model in l or "configFormula not exist" in l][-4:]
            if "Traceback" in mtxt or "loadFile" in mtxt:
                pass  # loadFile parse error 属正常噪声，不作判据
    return False, "TIMEOUT", excerpt


# ------------------------------------------------------------------
# 主流程
# ------------------------------------------------------------------
def backup_previous(qmt_dir, model):
    dst_dir = os.path.join(HERE, "_deploy_bak")
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    model_py = os.path.join(qmt_dir, "python", model + ".py")
    if not os.path.exists(model_py):
        log("无既有模型文件（首次部署）: %s" % model_py)
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(dst_dir, "%s.%s.py" % (model, stamp))
    shutil.copy2(model_py, dst)
    log("已备份旧模型 -> %s (%d bytes)" % (dst, os.path.getsize(dst)))
    # 只保留最近 10 份
    olds = sorted([f for f in os.listdir(dst_dir) if f.startswith(model + ".")],
                  key=lambda f: os.path.getmtime(os.path.join(dst_dir, f)))
    for f in olds[:-10]:
        try:
            os.remove(os.path.join(dst_dir, f))
        except Exception:
            pass
    return dst


def check_model_seeded(qmt_dir, model):
    """软检查：模型名是否出现在 indexUserConfig.xml（必要条件之一）。
    真正的判据是二进制 configFormula，脚本无法解析——最终以重启后是否出日志为准。"""
    xml = os.path.join(qmt_dir, "config", "indexUserConfig.xml")
    if not os.path.exists(xml):
        log("[WARN] 未找到 indexUserConfig.xml，跳过模型注册软检查")
        return True
    try:
        txt = open(xml, "r", encoding="utf-8", errors="replace").read()
    except Exception as e:
        log("[WARN] 读取 indexUserConfig.xml 失败: %s" % e)
        return True
    if ('name="%s"' % model) in txt or (">%s<" % model) in txt:
        log("模型注册软检查通过: %s 存在于 indexUserConfig.xml" % model)
        return True
    log("[WARN] %s 未出现在 indexUserConfig.xml —— 若从未在客户端 UI 里创建过该模型，"
        "重启后会被静默跳过（configFormula not exist）。" % model)
    return False


def main():
    ap = argparse.ArgumentParser(description="QMT 零介入部署器")
    ap.add_argument("--product", required=True, help="构建产物路径（GBK 单文件 .py）")
    ap.add_argument("--model", required=True, help="QMT 模型名（= python 目录下文件名，不含 .py）")
    ap.add_argument("--qmt-dir", required=True, help="QMT 客户端安装目录，如 D:\\QMT交易端模拟")
    ap.add_argument("--build", default=None, help="可选：先执行的构建脚本（相对项目目录）")
    ap.add_argument("--no-restart", action="store_true", help="只落盘不重启")
    ap.add_argument("--force-offhours", action="store_true", help="跳过交易时段闸（危险，需人工确认）")
    ap.add_argument("--timeout", type=int, default=300, help="等待 BUILD_TAG 的超时秒数")
    ap.add_argument("--dry-run", action="store_true", help="只做校验与打印，不落盘不重启")
    args = ap.parse_args()

    if not os.path.isdir(args.qmt_dir):
        fail(6, "QMT 目录不存在: %s" % args.qmt_dir)
    py_dir = os.path.join(args.qmt_dir, "python")
    if not os.path.isdir(py_dir):
        fail(6, "QMT python 目录不存在: %s" % py_dir)

    # 1) 时段闸（沙箱免闸）
    if not args.no_restart and in_block_window():
        if is_gate_exempt(args.qmt_dir):
            log("[INFO] 沙箱客户端免时段闸（纯验证目标，策略 passive 空转、无真钱、无盘中证据要保）"
                "——交易时段内照常部署/重启")
        elif args.force_offhours:
            log("[WARN] 处于交易时段（09:00-15:05），但因 --force-offhours 继续重启")
        else:
            fail(2, "当前处于交易时段（09:00-15:05），拒绝重启客户端。"
                    "请在收盘后 / 开盘前执行，或确认风险后加 --force-offhours。"
                    "（沙箱 D:\\QMT交易端模拟 已免闸，此拦截只会命中主号等承载在跑策略的客户端）")

    # 2) 可选构建
    if args.build:
        script = args.build if os.path.isabs(args.build) else os.path.join(HERE, args.build)
        if not os.path.exists(script):
            fail(6, "构建脚本不存在: %s" % script)
        log("执行构建: %s" % os.path.basename(script))
        rc = subprocess.call([sys.executable, script], cwd=HERE)
        if rc != 0:
            fail(3, "构建脚本返回非 0 (%d)" % rc)

    # 3) 产物校验
    product = args.product if os.path.isabs(args.product) else os.path.join(HERE, args.product)
    raw, tag = validate_product(product)
    log("产物校验通过: %s (%d bytes) BUILD_TAG=%s"
        % (os.path.basename(product), len(raw), tag))

    check_model_seeded(args.qmt_dir, args.model)

    if args.dry_run:
        log("[dry-run] 目标路径: %s" % os.path.join(py_dir, args.model + ".py"))
        return 0

    # 4) 备份 + 落盘
    backup_previous(args.qmt_dir, args.model)
    dst = os.path.join(py_dir, args.model + ".py")
    with open(dst, "wb") as f:
        f.write(raw)
    if os.path.getsize(dst) != len(raw):
        fail(3, "落盘后大小不符: %d != %d" % (os.path.getsize(dst), len(raw)))
    log("已落盘 -> %s (%d bytes)" % (dst, len(raw)))

    if args.no_restart:
        log("[OK] 已部署（未重启）。客户端下次启动/20:54 自重启后生效。")
        return 0

    # 5) 重启 + 等 TAG
    killed = kill_clients(args.qmt_dir)
    start_client(args.qmt_dir, exclude=killed)
    ok, reason, excerpt = wait_for_tag(args.qmt_dir, args.model, tag, timeout_sec=args.timeout)

    if excerpt:
        log("---- 日志摘录 ----")
        for line in excerpt:
            print("  " + line)
        sys.stdout.flush()

    if ok:
        log("[OK] PASS: 客户端已加载 %s，BUILD_TAG=%s" % (args.model, tag))
        return 0
    if reason == "MODEL_NOT_SEEDED":
        fail(4, "模型名 '%s' 未在客户端注册（configFormula not exist）。"
                "需先在 QMT 界面里手工创建一次同名模型（任意内容），之后才能全自动部署。" % args.model)
    fail(4, "超时未看到 BUILD_TAG=%s（%ds）。检查日志 %s"
            % (tag, args.timeout, log_paths(args.qmt_dir, time.strftime("%Y%m%d"))[0]))


if __name__ == "__main__":
    sys.exit(main() or 0)
