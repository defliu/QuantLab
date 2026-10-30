# coding: utf-8
"""Build the QMT Tushare probe: inject token + tag, emit a GBK single file.

Token comes from config/data_source_keys.json and is never written into the
repository - the product defaults to %TEMP%.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, "qmt_tushare_probe.py")
KEY_FILE = os.path.join(ROOT, "config", "data_source_keys.json")


def load_token():
    with open(KEY_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    tk = (cfg.get("sources") or {}).get("tushare", {}).get("token")
    if not tk or tk == "待填":
        raise SystemExit("tushare.token 未配置: %s" % KEY_FILE)
    return tk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()
    tag = args.tag or time.strftime("%Y%m%d-%H%M%S")
    token = load_token()
    if not re.match(r"^[A-Za-z0-9._-]+$", token):
        raise SystemExit("token 含非安全字符，拒绝写入")
    with open(TEMPLATE, encoding="utf-8") as f:
        text = f.read()
    if "@BUILD_TAG@" not in text or "@TS_TOKEN@" not in text:
        raise SystemExit("模板占位符缺失")
    text = text.replace("@BUILD_TAG@", tag).replace("@TS_TOKEN@", token)
    out = args.out or os.path.join(
        tempfile.gettempdir(), "qmt_tushare_probe_%s.py" % tag)
    raw = text.encode("gbk")
    if raw[:3] == b"\xef\xbb\xbf":
        raise SystemExit("产物意外带 BOM")
    first = raw.splitlines()[0].strip().replace(b" ", b"")
    if first != b"#coding=gbk":
        raise SystemExit("首行不是 # coding=gbk: %r" % first[:40])
    with open(out, "wb") as f:
        f.write(raw)
    print("built: %s (%d bytes) tag=%s token_len=%d"
          % (out, len(raw), tag, len(token)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
