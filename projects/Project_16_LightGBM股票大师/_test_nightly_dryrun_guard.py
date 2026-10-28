"""Test harness for the 2026-10-29 false-PASS hardening.

Two layers:
  A) python: qmt_nightly.write_report must not touch latest.json when deploy.dry_run,
     and must prefix the filename with REHEARSAL_.
  B) powershell semantics: the retry guard's JSON->decision expression
     (the injected .ps1 snippet is ASCII-only; the JSON fixtures are not, since
      deploy.result carries Chinese strings such as "FAIL（未出 BUILD_TAG）").
Console note: this harness echoes the PowerShell output, which contains U+FFFD once
utf-8 decoding met a non-utf8 byte. A GBK console cannot even print that -> the
UnicodeEncodeError used to kill the run before the RESULT line (2026-10-29 03:1x).
stdout is therefore reconfigured with errors="replace" so the checks still report.
Run: <venv python> _test_nightly_dryrun_guard.py
"""
import json
import os
import subprocess
import sys
import tempfile

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import qmt_nightly  # noqa: E402

fails = []


def check(name, cond):
    print(("  OK   " if cond else "  FAIL ") + name)
    if not cond:
        fails.append(name)


# ---------------- A) write_report dry-run semantics ----------------
tmp = tempfile.mkdtemp(prefix="qn_dry_")
orig_dir = qmt_nightly.REPORT_DIR
qmt_nightly.REPORT_DIR = tmp
try:
    scans = []
    # real report first: latest.json must exist and point at it
    md_real, js_real = qmt_nightly.write_report(
        "20261029-002304", {"model": "M", "rc": 4, "result": "FAIL(x)", "tag": "T1",
                            "detail": "", "dry_run": False}, scans, "FAIL")
    latest = os.path.join(tmp, "latest.json")
    check("real report writes latest.json", os.path.isfile(latest))
    snap = open(latest, encoding="utf-8").read()

    md_dry, js_dry = qmt_nightly.write_report(
        "20261029-010835", {"model": "M", "rc": 0, "result": "PASS", "tag": "T2",
                             "detail": "dry", "dry_run": True}, scans, "OK")
    check("dry-run report name has REHEARSAL_ prefix",
          os.path.basename(md_dry).startswith("REHEARSAL_"))
    check("dry-run report file created", os.path.isfile(js_dry))
    check("dry-run did NOT overwrite latest.json",
          open(latest, encoding="utf-8").read() == snap)
    d = json.load(open(md_dry.replace(".md", ".json"), encoding="utf-8"))
    check("dry_run flag persisted in json", d["deploy"]["dry_run"] is True)
    md_txt = open(md_dry, encoding="utf-8").read()
    check("md report advertises rehearsal", "REHEARSAL" in md_txt and "不算夜间闭环 PASS" in md_txt)
finally:
    qmt_nightly.REPORT_DIR = orig_dir


# ---------------- B) PowerShell guard semantics ----------------
PS = r'''
$ErrorActionPreference = "Continue"
$tmp = $args[0]
function Eval-Reuse([string]$file) {
    $d = Get-Content $file -Raw -Encoding UTF8 | ConvertFrom-Json
    $d0Deploy = if ($d.deploy) { [string]$d.deploy.result } else { "" }
    $d0Tag    = if ($d.deploy) { [string]$d.deploy.tag } else { "" }
    $d0Dry    = if ($d.deploy) { [bool]$d.deploy.dry_run } else { $false }
    $ok = ("PASS" -eq $d0Deploy) -and (-not $d0Dry)
    return "{0}|dry={1}|ok={2}" -f $d0Deploy, $d0Dry, $ok
}
foreach ($f in (Get-ChildItem -Path $tmp -Filter "*.json" | Sort-Object Name)) {
    Write-Host ("{0} => {1}" -f $f.Name, (Eval-Reuse $f.FullName))
}
'''

cases = {
    "a_old_real_pass_no_field.json": {"verdict": "OK", "deploy": {"result": "PASS", "tag": "T"}},
    "b_dry_run_pass.json": {"verdict": "OK", "deploy": {"result": "PASS", "tag": "T", "dry_run": True}},
    "c_real_pass_dryfalse.json": {"verdict": "ATTENTION", "deploy": {"result": "PASS", "tag": "T", "dry_run": False}},
    "d_fail.json": {"verdict": "FAIL", "deploy": {"result": "FAIL（未出 BUILD_TAG）", "tag": "T", "dry_run": False}},
    "e_deploy_null.json": {"verdict": "FAIL", "deploy": None},
}
psdir = tempfile.mkdtemp(prefix="qn_ps_")
for name, payload in cases.items():
    with open(os.path.join(psdir, name), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

# -Command "<script>" <arg> appends the arg as another command line, so the snippet
# must live in a file and be invoked with -File.
ps_file = os.path.join(psdir, "_eval_guard.ps1")
with open(ps_file, "w", encoding="ascii") as f:
    f.write(PS)

out = subprocess.run(
    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_file, psdir],
    capture_output=True, text=True, encoding="utf-8", errors="replace")
print(out.stdout.strip())
if out.stderr.strip():
    print("STDERR:", out.stderr.strip()[:400])

txt = out.stdout
check("old real PASS (no dry_run field) is reused", "a_old_real_pass_no_field.json => PASS|dry=False|ok=True" in txt)
check("dry-run PASS is rejected", "b_dry_run_pass.json => PASS|dry=False|ok=False" in txt
      or "b_dry_run_pass.json => PASS|dry=True|ok=False" in txt)
check("real PASS with ATTENTION is reused", "c_real_pass_dryfalse.json => PASS|dry=False|ok=True" in txt)
d_line = [l for l in txt.splitlines() if l.startswith("d_fail.json")]
check("FAIL is not reused", bool(d_line) and d_line[0].startswith("d_fail.json => FAIL")
      and "ok=False" in d_line[0])
check("deploy=null does not crash", "e_deploy_null.json => |dry=False|ok=False" in txt)
check("python raised no ConvertFrom-Json error", "ConvertFrom-Json" not in out.stderr)

print("\nRESULT %s (%d failures)" % ("ALL PASS" if not fails else "FAILURES", len(fails)))
if fails:
    for x in fails:
        print(" - " + x)
sys.exit(1 if fails else 0)
