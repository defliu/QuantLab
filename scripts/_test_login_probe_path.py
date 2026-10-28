# -*- coding: utf-8 -*-
"""qmt_login_probe 路径匹配与进程判定回归（只读，零副作用）

动机（2026-10-29 02:1x 实测踩到）：探针用 WMI 的 `ExecutablePath.StartsWith($d)` 判断
「这台客户端在不在跑」，而 WMI 永远返回反斜杠路径。传入 `D:/QMT交易端模拟`（正斜杠）时
匹配不到 ⇒ pid=None ⇒ 报 **EXIT=4「客户端进程不在（未启动）」**，而当时两台客户端明明在跑
（pid 6568 / 17932）。EXIT=4 会把排障方向带去「本机没启动/计划任务没拉起」，真根因却是券商侧
200003 登录超时（EXIT=2）——误报方向比漏报贵得多，故必须锁进测试。

覆盖：
  T1 四种写法（反斜杠 / 正斜杠 / 带尾分隔符 / 大小写混）都必须匹到同一个 pid
  T2/T2b 前缀边界：StartsWith 带尾分隔符 ⇒ 截断的目录名前缀不得误匹到真实客户端
  T3 禁扫真实交易客户端（正/反斜杠两种写法都要拒绝，rc=5）
  T4 兜底：进程匹配不到、但主日志 90 秒内仍在写 ⇒ rc=2 且状态不得出现「进程不在」
  T5 真没跑：不存在且无日志的目录 ⇒ rc=4（兜底不得把死目录也放行）

用法：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe \
      scripts/_test_login_probe_path.py
"""
import datetime as dt
import os
import shutil
import sys
import tempfile

# 控制台默认 GBK，正文里的 ⇒/─ 会 UnicodeEncodeError 吞掉后半轮结果 ⇒ 统一转 UTF-8 + 兜底替换
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qmt_login_probe import find_process_start, norm_client_dir, probe_client  # noqa: E402

SANDBOX = r"D:\QMT交易端模拟"
PROD = r"D:\国金QMT交易端模拟"
FORBIDDEN = r"D:\国金证券QMT交易端"

results = []


def check(name, ok, info=""):
    results.append((name, bool(ok), info))
    print("%s %-58s %s" % ("PASS" if ok else "FAIL", name, info))


def pid_of(path):
    pid, _start, err = find_process_start(path)
    return pid, err


# ---- T1 同一目录四种写法，pid 必须一致且非空 -------------------------------
running = None
for cand in (SANDBOX, PROD):
    pid0, _ = pid_of(cand)
    if pid0:
        running = cand
        break

if running is None:
    check("T1 四种写法匹配同一 pid（跳过：两台客户端均未运行）", True,
          "本机此刻没有 XtItClient 进程，无法做正例——改由 T4 的合成日志验证兜底分支")
else:
    variants = [
        running,
        running.replace("\\", "/"),
        running + "\\",
        running + "/",
        running[0].lower() + running[1:],
        os.path.join(running, "bin.x64", ".."),
    ]
    pids = []
    for v in variants:
        p, err = pid_of(v)
        pids.append(p)
        if err:
            check("T1 无 WMI 错误", False, "%s ⇒ %s" % (v, err))
    check("T1 四种写法匹配同一 pid（正例=%s）" % running,
          all(p == pids[0] and p is not None for p in pids),
          "变体=%s pids=%s" % (len(variants), sorted(set(str(x) for x in pids))))

# ---- T2 前缀边界：目录名前缀（缺边界）不得吃掉同前缀的兄弟目录 -------------
tmp_root = tempfile.mkdtemp(prefix="qmtprobe_")
try:
    base = os.path.join(tmp_root, "boundary_parent")
    os.makedirs(os.path.join(base, "userdata", "log"))
    p_base, _ = pid_of(base)
    check("T2 前缀边界：临时目录及其截断前缀都不得误匹到真实客户端",
          p_base is None, "parent=%s" % p_base)
    if running:
        cut = running[:-1]  # 少一个字 = 真目录名的前缀，缺分隔符边界
        p_cut, _ = pid_of(cut)
        check("T2b 截断前缀不匹配（StartsWith 带尾分隔符）",
              p_cut is None, "%s ⇒ pid=%s（非 None 即边界失效）" % (cut, p_cut))
    else:
        check("T2b 截断前缀不匹配（跳过：无运行中的客户端）", True, "无正例可比")

    # ---- T4 兜底：无进程但主日志刚写过 ⇒ rc=2，不得报「进程不在」 ----------
    today = dt.date.today().strftime("%Y%m%d")
    log = os.path.join(base, "userdata", "log", "XtClient_%s.log" % today)
    with open(log, "w", encoding="utf-8") as f:
        f.write("%s.000 [LOGIN] Proxy client not logged\n" % dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    r4, rc4 = probe_client(base)
    check("T4 日志新鲜但目录没匹到进程 ⇒ rc=2（不误报未启动）",
          rc4 == 2 and "不在" not in r4["state"], "rc=%s state=%s" % (rc4, r4["state"][:70]))

    # ---- T5 真没跑：既没进程也没有日志 ⇒ 仍须 rc=4 -----------------------
    dead = os.path.join(tmp_root, "nope")
    r5, rc5 = probe_client(dead)
    check("T5 无进程无日志 ⇒ rc=4（兜底不得放水）",
          rc5 == 4 and r5["state"] == "客户端进程不在（未启动）", "rc=%s state=%s" % (rc5, r5["state"]))
finally:
    shutil.rmtree(tmp_root, ignore_errors=True)

# ---- T3 禁扫真实交易客户端（两种写法）------------------------------------
for form in (FORBIDDEN, FORBIDDEN.replace("\\", "/")):
    r, rc = probe_client(form)
    check("T3 禁扫真实交易客户端 rc=5（%s）" % ("反斜杠" if "\\" in form else "正斜杠"),
          rc == 5, "rc=%s state=%s" % (rc, r["state"]))

# ---- 归一函数自身 --------------------------------------------------------
check("norm_client_dir 统一为正斜杠无关的反斜杠无尾串",
      norm_client_dir("D:/QMT交易端模拟/") == r"D:\QMT交易端模拟"
      and norm_client_dir(r"D:\QMT交易端模拟\\") == r"D:\QMT交易端模拟",
      repr(norm_client_dir("D:/QMT交易端模拟/")))

bad = [n for n, ok, _ in results if not ok]
print("\n==== %d/%d 项通过 ====" % (len(results) - len(bad), len(results)))
if bad:
    print("失败项: %s" % ", ".join(bad))
sys.exit(1 if bad else 0)
