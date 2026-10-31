# coding: utf-8
"""paper_forward_live.csv 的 source 语义回归（2026-11-01 审计整改）。

钉住三件事：
  ① 写入端显式落 source 列（不靠读侧 `or "forward"` 兜底）
  ② forward 是不可追溯属性：同日重跑传 backfill 不得把已记 forward 降级
  ③ 新增日期传 backfill 正常记为 backfill

另钉住统计侧排除 backfill（forward_stats / downgrade / exit / ab_stats / quarterly / rollback）。

运行：python _test_live_source_semantics.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import io  # noqa: E402

PASS = []
FAIL = []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, (" | " + detail) if detail else ""))


def _src(name):
    return io.open(os.path.join(HERE, name), encoding="utf-8").read()


def main():
    import pandas as pd
    import deploy_predict_g2 as D

    tmp = os.path.join(tempfile.gettempdir(), "_audit_live_source.csv")
    if os.path.exists(tmp):
        os.remove(tmp)
    orig = D.LIVE_LOG
    D.LIVE_LOG = tmp
    try:
        D._append_live_dedup([{"date": "2026-10-28", "code": "001395.SZ", "rank": 1,
                               "total_new": 66.5, "prob": 0.798}])
        d = pd.read_csv(tmp, encoding="utf-8-sig")
        ok("write.source_default_forward", d["source"].tolist() == ["forward"], str(d["source"].tolist()))

        D._append_live_dedup([{"date": "2026-10-28", "code": "001395.SZ", "rank": 1,
                               "total_new": 66.5, "prob": 0.798, "source": "backfill"}])
        d = pd.read_csv(tmp, encoding="utf-8-sig")
        ok("write.forward_not_downgraded", d["source"].tolist() == ["forward"], str(d["source"].tolist()))

        D._append_live_dedup([{"date": "2026-10-26", "code": "001395.SZ", "rank": 1,
                               "total_new": 60.5, "prob": 0.73, "source": "backfill"}])
        d = pd.read_csv(tmp, encoding="utf-8-sig")
        got = d.set_index("date")["source"].to_dict()
        ok("write.new_backfill_kept", got.get("2026-10-26") == "backfill", str(got))
        ok("write.rows_deduped", len(d) == 2, str(len(d)))
    finally:
        D.LIVE_LOG = orig
        if os.path.exists(tmp):
            os.remove(tmp)

    # 统计侧排除 backfill（静态断言，防止漏改）
    for f, tag in (("forward_stats.py", "stats"), ("paper_forward_downgrade.py", "downgrade"),
                   ("paper_forward_exit.py", "exit"), ("paper_forward_ab_stats.py", "ab"),
                   ("paper_quarterly_review.py", "quarterly"), ("rollback_check_g2.py", "rollback")):
        src = _src(f)
        ok("reader.%s_excludes_backfill" % tag,
           '!= "backfill"' in src or "!= 'backfill'" in src)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
