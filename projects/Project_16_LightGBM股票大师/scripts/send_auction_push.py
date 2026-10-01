import io
import os
import sys

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lark_push as LP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BODY = os.path.join(ROOT, "data", "cache", "_push_body_20260929.md")
OPEN_ID = "ou_bd13444d8ea53c28249c669f43f3eeff"

print("[CLI] resolved=%s exists=%s" % LP.describe_cli())

with io.open(BODY, "r", encoding="utf-8") as f:
    body = f.read()

if not body.strip():
    print("[PUSH-ERR] empty body")
    sys.exit(4)

ok = LP.send_text(
    body,
    uid=OPEN_ID,
    as_ident="bot",
    chunk_limit=None,
    retries=1,
    title="集合竞价预判 20260929",
)
print("[PUSH-RESULT] ok=%s" % ok)
sys.exit(0 if ok else 5)
