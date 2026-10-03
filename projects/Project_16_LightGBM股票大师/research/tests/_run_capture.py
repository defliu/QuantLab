# -*- coding: utf-8 -*-
"""通用 runner：在 Python 内部捕获子脚本 stdout/stderr，自写 UTF-8 报告。

PowerShell 的 *> 重定向在本机会截断大段输出（只留末行），
故测试结论以本 runner 落盘的报告为准。
"""
import io
import sys
import traceback

script = sys.argv[1]
report = sys.argv[2]

buf = io.StringIO()
code = 0
old_out, old_err = sys.stdout, sys.stderr
try:
    sys.stdout = buf
    sys.stderr = buf
    try:
        g = {"__name__": "__main__", "__file__": script}
        exec(compile(open(script, "rb").read(), script, "exec"), g)
    except SystemExit as e:
        code = int(e.code or 0)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
except Exception:
    buf.write("\n[RUNNER-EXC]\n")
    buf.write(traceback.format_exc())
    code = 3

text = buf.getvalue()
with io.open(report, "w", encoding="utf-8") as f:
    f.write(text)
    f.write("\n[RUNNER-EXIT]=%d\n" % code)

keep = []
for ln in text.splitlines():
    s = ln.strip()
    if (s.startswith("[FAIL]") or "PASS=" in ln or "RUNNER-" in s
            or s.startswith("- ") or "TEST-DONE" in s
            or s.startswith("[ERR") or s.startswith("[OK")
            or s.startswith("[RC]")):
        keep.append(ln)

sys.stdout.write("report=%s\nexit=%d\n" % (report, code))
sys.stdout.write("\n".join(keep[-80:]) + "\n")
sys.exit(0)