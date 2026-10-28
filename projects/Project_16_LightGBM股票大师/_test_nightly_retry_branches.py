"""ASCII-only branch-matrix harness for qmt_nightly_retry.ps1 (2026-10-29).

Why: the retry script's PASS branch had never actually run -- the broker login
outage means every real invocation so far took the WAIT path. A false PASS / lost
mark / card storm would only show up on the first real recovery night, i.e. at the
worst possible moment. This harness runs the REAL script against redirected paths
so every branch (PASS / ATTENTION / dry-run / stale report / SKIP / FAIL / WAIT /
wake-card / guards) executes offline with stubs.

Isolation: every path constant in a temp copy is rewritten to a fresh temp dir, so
production marks, counters, latest.json and the feishu pusher are never touched.
The stub pusher records cards to <tmp>/cards.jsonl instead of pushing.

Run: <venv python> _test_nightly_retry_branches.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "qmt_nightly_retry.ps1")
PYEXE = sys.executable

fails = []


def check(name, cond):
    print(("  OK   " + name) if cond else ("  FAIL " + name))
    if not cond:
        fails.append(name)


PROBE_STUB = r'''
import os, sys
d = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(d, "probe_calls.txt"), "a", encoding="utf-8") as f:
    f.write("call\n")
print("LOGGED=%s EXIT=%s NODE=1.2.3.4:56001 TCP=1 STATE=%s" % (
    os.environ.get("QMT_TEST_LOGGED", "0"),
    os.environ.get("QMT_TEST_EXIT", "2"),
    os.environ.get("QMT_TEST_STATE", "stub-state")))
sys.exit(0)
'''

PUSHER_STUB = r'''
import json, os, sys
d = os.path.dirname(os.path.abspath(__file__))
argv = sys.argv[1:]
def val(flag, default=""):
    return argv[argv.index(flag) + 1] if flag in argv else default
body = ""
bf = val("--body-file")
if bf and os.path.isfile(bf):
    body = open(bf, encoding="utf-8").read()
with open(os.path.join(d, "cards.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"title": val("--title"), "level": val("--level"),
                        "body": body}) + "\n")
print("stub-push-ok")
'''

# scenario -> what the stub "nightly loop" leaves behind in latest.json
NIGHTLY_STUB = r'''
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rep = Join-Path $dir "results\qmt_nightly"
New-Item -ItemType Directory -Force -Path $rep | Out-Null
Add-Content -Path (Join-Path $dir "nightly_calls.txt") -Value "call" -Encoding UTF8
$sc = $env:QMT_TEST_SCENARIO
$json = $null
$stale = $false
switch ($sc) {
    "pass_ok"       { $json = '{"ts":"stub","verdict":"OK","deploy":{"result":"PASS","tag":"TAGOK","dry_run":false}}' }
    "pass_attention"{ $json = '{"ts":"stub","verdict":"ATTENTION","deploy":{"result":"PASS","tag":"TAGATT","dry_run":false}}' }
    "pass_dryrun"   { $json = '{"ts":"stub","verdict":"OK","deploy":{"result":"PASS","tag":"TAGDRY","dry_run":true}}' }
    "stale_pass"    { $json = '{"ts":"stub","verdict":"OK","deploy":{"result":"PASS","tag":"TAGSTALE","dry_run":false}}'; $stale = $true }
    "fail_report"   { $json = '{"ts":"stub","verdict":"FAIL","deploy":{"result":"FAIL","tag":"","dry_run":false}}' }
    default         { }
}
if ($json) {
    $p = Join-Path $rep "latest.json"
    [IO.File]::WriteAllText($p, $json, (New-Object Text.UTF8Encoding($false)))
    if ($stale) { (Get-Item $p).LastWriteTime = (Get-Date).AddHours(-2) }
}
exit ([int]($env:QMT_TEST_RC))
'''


def build_env(tmp, logged=None, exit_code=None, state=None, scenario=None,
              rc=0, hhmm=None):
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    if logged is not None:
        env["QMT_TEST_LOGGED"] = str(logged)
    if exit_code is not None:
        env["QMT_TEST_EXIT"] = str(exit_code)
    if state is not None:
        env["QMT_TEST_STATE"] = state
    if scenario is not None:
        env["QMT_TEST_SCENARIO"] = scenario
    env["QMT_TEST_RC"] = str(rc)
    env["QMT_TEST_HHMM"] = hhmm or ""
    return env


def new_case():
    """Fresh temp tree with the real script (paths redirected) + stubs."""
    tmp = os.path.join(tempfile.mkdtemp(prefix="qn_retry_"), "proj")
    os.makedirs(os.path.join(tmp, "data", "schedules"))
    os.makedirs(os.path.join(tmp, "results", "qmt_nightly"))
    src = open(SRC, encoding="utf-8-sig").read()
    subs = [
        (r"^\$dir\s*=.*$", '$dir      = "%s"' % tmp.replace("\\", "/")),
        (r"^\$sandbox\s*=.*$", '$sandbox  = "%s/fake_client"' % tmp.replace("\\", "/")),
        (r"^\$probe\s*=.*$", '$probe    = "%s/probe_stub.py"' % tmp.replace("\\", "/")),
        (r"^\$nightly\s*=.*$", '$nightly  = "%s/nightly_stub.ps1"' % tmp.replace("\\", "/")),
        (r"^\$py\s*=.*$", '$py = "%s"' % PYEXE.replace("\\", "/")),
        (r'^\$hhmm = Get-Date -Format "HHmm"$',
         '$hhmm = $(if ($env:QMT_TEST_HHMM) { $env:QMT_TEST_HHMM } '
         'else { Get-Date -Format "HHmm" })'),
    ]
    for pat, rep in subs:
        src, n = re.subn(pat, rep, src, count=1, flags=re.M)
        if n != 1:
            raise AssertionError("production script drifted, pattern not found: " + pat)
    script = os.path.join(tmp, "qmt_nightly_retry.ps1")
    with open(script, "w", encoding="utf-8-sig") as f:   # keep BOM, PS 5.1 needs it
        f.write(src)
    with open(os.path.join(tmp, "probe_stub.py"), "w", encoding="utf-8") as f:
        f.write(PROBE_STUB)
    with open(os.path.join(tmp, "push_alert_card.py"), "w", encoding="utf-8") as f:
        f.write(PUSHER_STUB)
    with open(os.path.join(tmp, "nightly_stub.ps1"), "w", encoding="utf-8") as f:
        f.write(NIGHTLY_STUB)
    return tmp, script


def seed_report(tmp, text, age_hours=2.0):
    """Pre-place results/qmt_nightly/latest.json (the reuse guard reads it BEFORE
    the probe, so the nightly stub never runs on that path -- the fixture has to be
    planted directly)."""
    p = os.path.join(tmp, "results", "qmt_nightly", "latest.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    ts = time.time() - age_hours * 3600
    os.utime(p, (ts, ts))
    return p


class Case(object):
    def __init__(self, tmp):
        self.tmp = tmp

    def path(self, *p):
        return os.path.join(self.tmp, *p)

    def cards(self):
        fp = self.path("cards.jsonl")
        if not os.path.isfile(fp):
            return []
        return [json.loads(l) for l in open(fp, encoding="utf-8").read().splitlines() if l.strip()]

    def log(self):
        fp = self.path("data", "schedules", "qmt_nightly_retry.log")
        return open(fp, encoding="utf-8").read() if os.path.isfile(fp) else ""

    def marks(self):
        d = self.path("data", "schedules")
        return sorted(x for x in os.listdir(d) if x.endswith(".mark"))

    def probe_called(self):
        return os.path.isfile(self.path("probe_calls.txt"))

    def nightly_called(self):
        return os.path.isfile(self.path("nightly_calls.txt"))

    def attempts(self):
        d = self.path("data", "schedules")
        for x in os.listdir(d):
            if x.startswith("qmt_nightly_retry_attempts_"):
                return open(os.path.join(d, x), encoding="utf-8-sig").read().strip()
        return None


def run(script, env, tmp):
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", script], env=env, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=180)
    return Case(tmp), r.returncode


print("== branch matrix: qmt_nightly_retry.ps1 ==")

# --- C1 first-ever-executed PASS branch ------------------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_ok"), tmp)
check("C1 PASS: exit 0", rc == 0)
check("C1 PASS: daily mark written", c.marks() == [x for x in c.marks()
      if "qmt_nightly_pass_" in x])
mk = [x for x in c.marks() if "qmt_nightly_pass_" in x]
mk_body = open(c.path("data", "schedules", mk[0]), encoding="utf-8-sig").read() if mk else ""
check("C1 PASS: mark carries BUILD_TAG TAGOK", "TAGOK" in mk_body)
check("C1 PASS: exactly 1 info card", len(c.cards()) == 1 and c.cards()[0]["level"] == "info")
check("C1 PASS: card body carries TAGOK", "TAGOK" in c.cards()[0]["body"])
check("C1 PASS: attempts counter = 1", c.attempts() == "1")

# --- C2 verdict=ATTENTION must still count as PASS --------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_attention"), tmp)
check("C2 ATTENTION: still PASS (mark written)", any("qmt_nightly_pass_" in x for x in c.marks()))
check("C2 ATTENTION: card mentions ATTENTION",
      len(c.cards()) == 1 and "ATTENTION" in c.cards()[0]["body"])

# --- C3 fresh dry-run PASS must NOT be taken --------------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_dryrun"), tmp)
check("C3 dry-run: no mark", not any("qmt_nightly_pass_" in x for x in c.marks()))
check("C3 dry-run: not silently success (rc!=0)", rc != 0)
check("C3 dry-run: error card (FAIL branch)",
      len(c.cards()) == 1 and c.cards()[0]["level"] == "error")

# --- C4 stale latest.json (loop crashed, no report this round) --------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="stale_pass"), tmp)
check("C4 stale report: no mark", not any("qmt_nightly_pass_" in x for x in c.marks()))
check("C4 stale report: no card (SKIP, not FAIL)", len(c.cards()) == 0)
check("C4 stale report: exit 0", rc == 0)

# --- C5 nightly wrote nothing at all ---------------------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="none"), tmp)
check("C5 no report at all: no card, no mark", len(c.cards()) == 0
      and not any("qmt_nightly_pass_" in x for x in c.marks()))

# --- C6 genuine FAIL report -------------------------------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="fail_report", rc=4), tmp)
check("C6 FAIL: exit 1", rc == 1)
check("C6 FAIL: error card", len(c.cards()) == 1 and c.cards()[0]["level"] == "error")
check("C6 FAIL: no mark", not any("qmt_nightly_pass_" in x for x in c.marks()))

# --- C7 WAIT outside the wake window: zero cards ----------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=0, exit_code=2, hhmm="0145"), tmp)
check("C7 WAIT 01:45: no card, no mark, exit 0",
      len(c.cards()) == 0 and not c.marks() and rc == 0)
check("C7 WAIT: loop NOT invoked", not c.nightly_called())
check("C7 WAIT: attempts not counted", c.attempts() is None)

# --- C8/C9 wake card at window close + daily dedupe -------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=0, exit_code=2, hhmm="0835",
                         state="TCP open, counter silent 120s"), tmp)
check("C8 wake 08:35: one warn card", len(c.cards()) == 1 and c.cards()[0]["level"] == "warn")
check("C8 wake: body cites the single login node",
      "180.169.107.19" in c.cards()[0]["body"])
check("C8 wake: dedupe mark created",
      any("qmt_retry_wakecard_" in x for x in c.marks()))
c, rc = run(s, build_env(tmp, logged=0, exit_code=2, hhmm="0845"), tmp)
check("C9 wake 08:45 same day: no second card", len(c.cards()) == 1)

# --- C10 EXIT=4 wake card must be blamed locally, not on the broker ---------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=0, exit_code=4, hhmm="0840",
                         state="client process not running"), tmp)
check("C10 wake EXIT=4: warn card", len(c.cards()) == 1 and c.cards()[0]["level"] == "warn")
b = c.cards()[0]["body"] if c.cards() else ""
check("C10 wake EXIT=4: body says EXIT=4", "EXIT=4" in b)
check("C10 wake EXIT=4: body must not claim the broker node", "180.169.107.19" not in b)

# --- C11 intraday time gate runs before any deploy --------------------------
tmp, s = new_case()
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_ok", hhmm="1000"), tmp)
check("C11 time gate 10:00: probe and loop never invoked",
      not c.probe_called() and not c.nightly_called() and rc == 0)

# --- C12 already PASSed today: fully silent --------------------------------
tmp, s = new_case()
today = time.strftime("%Y%m%d")
open(s.replace("qmt_nightly_retry.ps1", "data\\schedules\\qmt_nightly_pass_%s.mark" % today),
     "w", encoding="utf-8").write("earlier")
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_ok"), tmp)
check("C12 daily mark present: silent exit, nothing runs",
      not c.probe_called() and not c.nightly_called() and len(c.cards()) == 0 and rc == 0)

# --- C13 kill switch --------------------------------------------------------
tmp, s = new_case()
open(os.path.join(tmp, "data", "schedules", "qmt_retry_off"), "w").write("1")
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_ok"), tmp)
check("C13 off switch: nothing runs", not c.probe_called() and len(c.cards()) == 0 and rc == 0)

# --- C14 6-attempt cap ------------------------------------------------------
tmp, s = new_case()
open(os.path.join(tmp, "data", "schedules",
                  "qmt_nightly_retry_attempts_%s.txt" % today), "w", encoding="utf-8").write("6")
c, rc = run(s, build_env(tmp, logged=1, exit_code=0, scenario="pass_ok"), tmp)
check("C14 cap 6: loop not invoked, error card", not c.nightly_called()
      and len(c.cards()) == 1 and c.cards()[0]["level"] == "error")
check("C14 cap 6: counter unchanged", c.attempts() == "6")

# --- C15 reuse a recent real PASS instead of restarting the client ----------
tmp, s = new_case()
seed_report(tmp, '{"ts":"seed","verdict":"OK","deploy":{"result":"PASS","tag":"TAGSEED","dry_run":false}}')
c, rc = run(s, build_env(tmp, logged=0, exit_code=2), tmp)
check("C15 reuse: mark written, info card, loop skipped",
      any("qmt_nightly_pass_" in x for x in c.marks()) and len(c.cards()) == 1
      and c.cards()[0]["level"] == "info" and not c.nightly_called())
check("C15 reuse: mark carries the seeded TAG", "TAGSEED" in
      open([os.path.join(tmp, "data", "schedules", x) for x in c.marks()
            if "qmt_nightly_pass_" in x][0], encoding="utf-8-sig").read())
check("C15 reuse: probe skipped (guard 0 fires first)", not c.probe_called())

# --- C16 a recent REHEARSAL PASS must not be reused -------------------------
tmp, s = new_case()
seed_report(tmp, '{"ts":"seed","verdict":"OK","deploy":{"result":"PASS","tag":"TAGDRYSEED","dry_run":true}}')
c, rc = run(s, build_env(tmp, logged=0, exit_code=2, hhmm="0145"), tmp)
check("C16 rehearsal report not reused: no mark",
      not any("qmt_nightly_pass_" in x for x in c.marks()))
check("C16 rehearsal: falls through to WAIT (probe ran, loop skipped, no card)",
      c.probe_called() and not c.nightly_called() and len(c.cards()) == 0)

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
