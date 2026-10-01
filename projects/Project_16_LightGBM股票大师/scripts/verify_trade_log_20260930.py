# coding: utf-8
"""校验成交账本完整性：表头、字段数、account_id 戳、时间格式、603237 补记行。

只读，不修改账本。任一 FAIL 以非零退出码结束（供人工拦截）。
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qmt_config as C

FIELDS = ["time", "code", "side", "vol", "price", "score", "order_id", "account_id"]
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def main():
    fails = []
    with open(C.TRADE_LOG, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    if header != FIELDS:
        fails.append("表头不符: %s" % header)

    for i, r in enumerate(rows, start=2):
        if len(r) != len(FIELDS):
            fails.append("L%d 字段数=%d(应%d): %s" % (i, len(r), len(FIELDS), r))
            continue
        d = dict(zip(FIELDS, r))
        if not TIME_RE.match(d["time"]):
            fails.append("L%d 时间格式异常: %r" % (i, d["time"]))
        if (d["account_id"] or "").strip() != C.ACCOUNT_ID:
            fails.append("L%d 账号戳不符: %r (应 %s)" % (i, d["account_id"], C.ACCOUNT_ID))

    print("账本: %s" % C.TRADE_LOG)
    print("总行数: %d" % len(rows))
    print("账号戳(%s)校验: %s" % (C.ACCOUNT_ID, "PASS" if not fails else "FAIL"))

    hit = [dict(zip(FIELDS, r)) for r in rows if len(r) == len(FIELDS) and r[1] == "603237.SH" and r[0].startswith("2026-09-30")]
    print("2026-09-30 的 603237.SH 记录: %s" % (hit if hit else "缺失"))

    if fails:
        print("\nFAIL明细:")
        for x in fails:
            print("  - %s" % x)
        return 1
    print("\n全部字段校验 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())