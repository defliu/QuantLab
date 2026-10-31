"""factor_design_weekend.ps1 分支矩阵回归（AGENTS.md「成功分支从未执行过」硬规则）。

做法：把真实脚本按正则改写路径常量复制到临时目录，配桩（deveco / 推送），
任一替换匹配不到即 AssertionError（防生产脚本漂移把测试变成假绿）。
跑完复核生产目录零副作用。

覆盖分支：
  L1 启动失败            -> 127
  L2 产物落盘后正常退出   -> 0
  L3 产物落盘但进程残留   -> 0（新修：不得被 124 覆盖）
  L4 exit 0 但无产物      -> 2（旧版假成功）
  L5 非零退出且无产物     -> 3（透传）
  L6 超时无产物           -> 124
  L7 拿不到 ExitCode 且无产物 -> 1
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

SRC = r"D:\QuantLab\research_factor_mining\factor_design_weekend.ps1"
SCHED = r"D:\QuantLab\data\schedules"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  " + name)
    else:
        print("  FAIL  " + name + ("  " + detail if detail else ""))
        FAILS.append(name)


def sub_once(text, pattern, repl, label):
    """正则替换，必须恰好命中 1 次。"""
    new, n = re.subn(pattern, repl, text, flags=re.M)
    if n != 1:
        raise AssertionError("替换 %s 命中 %d 次（必须 1 次）：%s" % (label, n, pattern))
    return new


def snapshot(dirpath):
    out = {}
    if not os.path.isdir(dirpath):
        return out
    for fn in os.listdir(dirpath):
        p = os.path.join(dirpath, fn)
        if os.path.isfile(p):
            st = os.stat(p)
            out[fn] = (st.st_size, int(st.st_mtime))
    return out


def build_case(work, stub_path, py_push, deadline_min, launch_fail=False, null_ec=False):
    src = open(SRC, "rb").read().decode("utf-8-sig")
    t = sub_once(src, r'^\$schedDir = ".*"$',
                 '$schedDir = "%s"' % work.replace("\\", "\\\\"), "schedDir")
    t = sub_once(t, r'^\$fmDir = ".*"$',
                 '$fmDir = "%s"' % os.path.join(work, "fm").replace("\\", "\\\\"), "fmDir")
    t = sub_once(t, r'^\$pyPush = ".*"$',
                 '$pyPush = "%s"' % py_push.replace("\\", "\\\\"), "pyPush")
    t = sub_once(t, r'AddMinutes\(50\)', 'AddMinutes(%s)' % deadline_min, "deadline")
    t = sub_once(t, r'Start-Sleep -Seconds 30', 'Start-Sleep -Seconds 1', "poll")
    # 这段在 PowerShell 双引号字符串内部：绝不能再塞双引号（会截断字面量）。
    # 临时目录路径无空格，直接裸写即可。
    t = sub_once(t, r'--dir D:\\QuantLab --format json',
                 '--dir %s --format json' % work.replace("\\", "\\\\"), "dir")
    if launch_fail:
        t = sub_once(t, r'^\$psi\.FileName = \$deveco$',
                     '$psi.FileName = "%s"' % os.path.join(work, "no_such_deveco.exe").replace("\\", "\\\\"),
                     "fileName(fail)")
    else:
        t = sub_once(t, r'^\$psi\.FileName = \$deveco$',
                     '$psi.FileName = "%s"' % POWERSHELL.replace("\\", "\\\\"), "fileName")
    t = sub_once(t, r'^\$psi\.Arguments = \$argLine$',
                 '$psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"%s`" " + $argLine'
                 % stub_path.replace("\\", "\\\\"), "arguments")
    if null_ec:
        t = sub_once(t, r'try \{ \$ec = \$proc\.ExitCode \} catch \{ \}',
                     'try { $ec = $null } catch { }', "nullExitCode")
    path = os.path.join(work, "run.ps1")
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + t.encode("utf-8"))
    return path


STUB = r"""
$out = $env:BM_OUT
switch ($env:BM_MODE) {
  'artifact'           { [System.IO.File]::WriteAllText($out, ('x' * 3000)); exit 0 }
  'artifact_then_slow' { [System.IO.File]::WriteAllText($out, ('x' * 3000)); Start-Sleep -Seconds 60; exit 0 }
  'nofile'             { exit 0 }
  'nofile3'            { exit 3 }
  'slow'               { Start-Sleep -Seconds 60; exit 0 }
  default              { exit 9 }
}
"""


def run_case(label, mode, want_rc, deadline_min, launch_fail=False, null_ec=False,
             want_push=None):
    print("\n[" + label + "] mode=" + mode + " deadline=" + str(deadline_min) + "min")
    work = tempfile.mkdtemp(prefix="fm_bm_")
    try:
        os.makedirs(os.path.join(work, "fm", "candidates"), exist_ok=True)
        stub = os.path.join(work, "deveco_stub.ps1")
        with open(stub, "wb") as f:
            f.write(b"\xef\xbb\xbf" + STUB.encode("utf-8"))
        push_log = os.path.join(work, "push.log")
        py_push = ("import sys; open(r'%s','a',encoding='utf-8').write(sys.argv[1]+chr(10))"
                   % push_log.replace("\\", "/"))
        date = time.strftime("%Y%m%d")
        out_file = os.path.join(work, "de_factor_mining_r3_" + date + ".md")
        env = dict(os.environ)
        env["BM_MODE"] = mode
        env["BM_OUT"] = out_file
        script = build_case(work, stub, py_push, deadline_min, launch_fail, null_ec)
        t0 = time.time()
        p = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", script], env=env, capture_output=True)
        rc = p.returncode
        el = time.time() - t0
        check(label + " rc=" + str(want_rc), rc == want_rc,
              "实际 rc=%d (%.1fs) stderr=%s" % (rc, el, p.stderr.decode("utf-8", "replace")[:400]))

        logs = [f for f in os.listdir(work) if f.startswith("factor_design_") and f.endswith(".log")]
        body = ""
        for f in logs:
            body += open(os.path.join(work, f), encoding="utf-8", errors="replace").read()
        push = ""
        if os.path.exists(push_log):
            push = open(push_log, encoding="utf-8", errors="replace").read()

        if want_push is not None:
            check(label + " push含'" + want_push + "'", want_push in push,
                  "push=" + repr(push[:300]))
        if mode == "artifact" or mode == "artifact_then_slow":
            check(label + " 产物存在", os.path.exists(out_file) and os.path.getsize(out_file) >= 3000)
        check(label + " 日志已写", len(body) > 0)
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    before = snapshot(SCHED)
    print("=== 生产目录快照 %d 个文件 ===" % len(before))

    run_case("L1 启动失败", "nofile", 127, 0.03, launch_fail=True, want_push="异常 exit=127")
    run_case("L2 产物落盘正常退出", "artifact", 0, 0.03, want_push="完成 exit=0")
    run_case("L3 产物落盘进程残留", "artifact_then_slow", 0, 0.03, want_push="完成 exit=0")
    run_case("L4 exit0无产物", "nofile", 2, 0.03, want_push="异常 exit=2")
    run_case("L5 非零退出无产物", "nofile3", 3, 0.03, want_push="异常 exit=3")
    run_case("L6 超时无产物", "slow", 124, 0.03, want_push="异常 exit=124")
    run_case("L7 ExitCode不可读且无产物", "nofile", 1, 0.03, null_ec=True, want_push="异常 exit=1")

    after = snapshot(SCHED)
    check("生产目录零副作用", before == after,
          "新增/改动: %s" % sorted(set(after) - set(before)))

    print("\n=== %d 项失败 ===" % len(FAILS))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
