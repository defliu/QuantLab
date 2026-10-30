"""Launch a command with no visible console window and propagate its exit code.

Why this exists: Windows 11 delegates consoles to Windows Terminal, which
ignores `powershell.exe -WindowStyle Hidden` and still pops a window. A
`pythonw.exe` parent is a GUI-subsystem process that never owns a console,
so children started with CREATE_NO_WINDOW stay fully invisible while the
Task Scheduler task itself remains Interactive (required for the QMT GUI
clients). GUI children (XtItClient.exe) are unaffected: CREATE_NO_WINDOW
only suppresses console allocation.

Usage:
    pythonw.exe run_hidden.py [--log <path>] -- <exe> [args...]

The child is started from an argument list (no shell), so quotes and
non-ASCII paths need no escaping. Exit code of the child becomes the
launcher's exit code, which Task Scheduler records as LastTaskResult.
Child stdout/stderr go to --log (rotated at 5 MB) or are discarded.
"""
import os
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000
LOG_MAX_BYTES = 5 * 1024 * 1024


def rotate_log(path):
    try:
        if os.path.exists(path) and os.path.getsize(path) >= LOG_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass


def main(argv):
    args = argv[1:]
    log_path = ""
    if args and args[0] == "--log":
        if len(args) < 2:
            return 3
        log_path = args[1]
        args = args[2:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        return 3

    child_env = dict(os.environ)
    child_env["PYTHONUNBUFFERED"] = "1"

    log_file = None
    out = subprocess.DEVNULL
    if log_path:
        rotate_log(log_path)
        try:
            log_file = open(log_path, "a", encoding="utf-8", errors="replace")
            out = log_file
        except OSError:
            log_file = None
            out = subprocess.DEVNULL

    try:
        rc = subprocess.call(args, stdout=out, stderr=subprocess.STDOUT,
                             creationflags=CREATE_NO_WINDOW, env=child_env)
    except Exception as exc:  # noqa: BLE001 - report, never crash silently
        if log_file is not None:
            log_file.write("[run_hidden] launch failed: %s\n" % exc)
            log_file.flush()
        return 4
    finally:
        if log_file is not None:
            log_file.close()
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
