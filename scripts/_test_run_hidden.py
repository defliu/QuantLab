"""Regression for scripts/run_hidden.py + the Interactive-task rewrite.

Why: on Windows 11 the console is delegated to Windows Terminal, which
ignores `powershell.exe -WindowStyle Hidden` -- tasks kept popping windows
while claiming to be hidden. The fix routes those tasks through
`pythonw.exe run_hidden.py`, which has no console of its own and starts the
child with CREATE_NO_WINDOW. This pins the three properties that make that
safe:

  H1  no visible window is created while the child runs
  H2  the child's exit code propagates (Task Scheduler LastTaskResult)
  H3  child stdout still reaches --log (the tasks' only diagnostics)
  H4  a GUI child still gets its window (QMT clients must stay visible)
  T1  every Interactive task in production launches via run_hidden.py
  T2  those tasks kept Interactive + Highest (S4U would hide the login box)
  T3  no task regressed to the ineffective `-WindowStyle Hidden` trick

Run: <venv python> scripts/_test_run_hidden.py
"""
import os
import re
import subprocess
import sys
import tempfile

PYW = r"D:\Python311\pythonw.exe"
PY = r"D:\Python311\python.exe"
LAUNCHER = r"D:\QuantLab\scripts\run_hidden.py"

# Tasks that must restart the QMT GUI clients: Interactive is mandatory
# (S4U puts them in session 0, where nobody can see or click the login box).
INTERACTIVE_TASKS = [
    "QMT_WatchLogin_10min",
    "QMT_Prod_LogonStart",
    "QMT_Sandbox_LogonStart",
    "Quant_P16_QMT_NightlyDeploy",
    "Quant_P16_QMT_NightlyRetry",
]

WINDOW_PROBE = r'''
import ctypes, sys, time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

def visible_titles():
    out = []
    def cb(hwnd, lparam):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append(buf.value)
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out

marker = sys.argv[1]
deadline = time.time() + 5
seen = False
while time.time() < deadline:
    if marker in visible_titles():
        seen = True
        break
    time.sleep(0.2)
print("GUI_VISIBLE=%d" % (1 if seen else 0))
'''

fails = []


def check(name, cond):
    print(("  OK   " + name) if cond else ("  FAIL " + name))
    if not cond:
        fails.append(name)


def run_launcher(args, timeout=30):
    return subprocess.run([PYW, LAUNCHER] + args, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def count_windows():
    ps = ("(Get-Process | Where-Object { $_.MainWindowTitle } | "
          "Measure-Object).Count")
    r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                       capture_output=True, encoding="utf-8", errors="replace")
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


print("== H: run_hidden.py behaviour ==")

# H1 -- no visible window appears while a console child runs
before = count_windows()
proc = subprocess.Popen([PYW, LAUNCHER, "--", "cmd.exe", "/c", "ping -n 6 127.0.0.1 > nul"])
peak = before
while proc.poll() is None:
    peak = max(peak, count_windows())
    if peak > before:
        break
    import time as _t
    _t.sleep(0.25)
proc.wait(30)
check("H1 no visible window created", peak == before)

# H2 -- exit code propagation (both success and failure)
r = run_launcher(["--", "cmd.exe", "/c", "exit", "0"])
check("H2 rc 0 propagates", r.returncode == 0)
r = run_launcher(["--", "cmd.exe", "/c", "exit", "7"])
check("H2 rc 7 propagates", r.returncode == 7)
r = run_launcher(["--", "cmd.exe", "/c", "exit", "1"])
check("H2 rc 1 propagates", r.returncode == 1)

# H3 -- stdout still lands in --log
tmp = tempfile.mkdtemp(prefix="runhidden_")
logp = os.path.join(tmp, "child.log")
r = run_launcher(["--log", logp, "--", "cmd.exe", "/c", "echo HELLO_HIDDEN"])
body = open(logp, encoding="utf-8", errors="replace").read() if os.path.isfile(logp) else ""
check("H3 child stdout reaches --log", "HELLO_HIDDEN" in body)

# H4 -- GUI child still gets a visible window
# Two processes: the tkinter child runs through the launcher, a console probe
# (also through the launcher, so it has no window of its own) polls for it.
marker = "RUNHIDDEN_GUI_MARKER"
probe = os.path.join(tmp, "gui_probe.py")
open(probe, "w", encoding="utf-8").write(WINDOW_PROBE)
gui = os.path.join(tmp, "gui.py")
open(gui, "w", encoding="utf-8").write(
    "import tkinter, sys\n"
    "r = tkinter.Tk()\n"
    "r.title(sys.argv[1])\n"
    "r.geometry('300x120')\n"
    "r.after(6000, r.destroy)\n"
    "r.mainloop()\n")
gui_proc = subprocess.Popen([PYW, LAUNCHER, "--", PY, gui, marker])
# The launcher redirects child stdout (to --log, else DEVNULL), so the probe's
# verdict has to be read from the log rather than from the launcher's stdout.
probe_log = os.path.join(tmp, "probe.log")
r = run_launcher(["--log", probe_log, "--", PY, probe, marker], timeout=40)
gui_proc.wait(40)
probe_out = open(probe_log, encoding="utf-8", errors="replace").read() if os.path.isfile(probe_log) else ""
check("H4 GUI child stays visible through launcher", "GUI_VISIBLE=1" in probe_out)

# T1-T3 -- production task definitions
print("== T: production Interactive task definitions ==")
ps = (
    "$tasks = Get-ScheduledTask | Where-Object { $_.TaskName -in @(%s) };"
    "$tasks | ForEach-Object {"
    "  $a = $_.Actions[0];"
    "  Write-Output ($_.TaskName + '|' + $a.Execute + '|' + $a.Arguments + '|' +"
    "    $_.Principal.LogonType + '|' + $_.Principal.RunLevel) }"
) % (",".join("'%s'" % t for t in INTERACTIVE_TASKS))
r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                   capture_output=True, encoding="utf-8", errors="replace")
rows = {}
for line in r.stdout.splitlines():
    parts = line.split("|")
    if len(parts) == 5 and parts[0] in INTERACTIVE_TASKS:
        rows[parts[0]] = {"exec": parts[1], "args": parts[2],
                          "logon": parts[3], "run": parts[4]}

check("T0 all %d tasks found" % len(INTERACTIVE_TASKS), len(rows) == len(INTERACTIVE_TASKS))
for name in INTERACTIVE_TASKS:
    row = rows.get(name)
    if not row:
        check("T1 %s found" % name, False)
        continue
    check("T1 %s launches via run_hidden.py" % name,
          row["exec"].lower().endswith("pythonw.exe") and "run_hidden.py" in row["args"])
    check("T2 %s kept Interactive + Highest" % name,
          row["logon"] == "Interactive" and row["run"] == "Highest")
    check("T3 %s has no -WindowStyle Hidden crutch" % name,
          "-WindowStyle" not in row["args"])

# T4 -- the hub launcher must not fall back to cmd.exe (that is what popped windows)
hub = r"D:\QuantLab\scripts\qmt_signal_hub.ps1"
src = open(hub, encoding="utf-8-sig").read()
check("T4 hub spawns via run_hidden.py", "run_hidden.py" in src)
check("T4 hub no longer spawns cmd.exe for the hub",
      not re.search(r"$cmdLine\s*=\s*'cmd\.exe", src, re.M))

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), "; ".join(fails)))
    sys.exit(1)
print("ALL PASS")
