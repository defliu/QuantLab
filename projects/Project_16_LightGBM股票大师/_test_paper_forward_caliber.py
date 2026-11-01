# -*- coding: utf-8 -*-
"""_test_paper_forward_caliber.py — 纸面前向 caliber 打标回归（2026-11-01）。

背景：
  `build_g2_daily` 的 amount 单位缺陷（增量库元 vs 主库千元，差 1000 倍）使 43f 模型的
  amount_ma5 特征在 8-28~10-31 期间 100% 失效（1212 个分裂阈值全落 8.75~14.30，元口径值
  18.34 恒落同一侧）。修复于 commit a9cc881（2026-10-31）。

  打标目的：区分「收益有效但选股脏」的历史批次，防止污染样本进入 OOS 统计。
  本测试钉住三件事，防止打标机制被后续改动破坏：

  T1  日期分类正确（clean / contaminated_selection / forward 边界）
  T2  两个 CSV 都带 caliber 列，且行数未变（打标只加列不改数）
  T3  统计/闸门脚本确实排除了 contaminated_selection（读源码检查）
  T4  收益数值未被打标改动（与 .bak_20261101_caliber 备份逐笔比对）
  T5  写入侧新记录默认 caliber=forward（防止新样本掉进 NaN 后被误算）

用法：python _test_paper_forward_caliber.py   （0=全过，1=有失败）
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tag_paper_forward_caliber as TG  # noqa: E402

REAL = os.path.join(HERE, "data", "real")
LIVE = os.path.join(REAL, "paper_forward_live.csv")
SHADOW = os.path.join(REAL, "paper_shadow_b21_picks.csv")
LIVE_BAK = LIVE + ".bak_20261101_caliber"
SHADOW_BAK = SHADOW + ".bak_20261101_caliber"

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        FAILS.append(name)


def test_classify():
    print("[T1] 日期分类边界")
    cases = [
        ("2026-08-17", "clean"),
        ("2026-08-27", "clean"),
        ("2026-08-28", "contaminated_selection"),
        ("2026-10-31", "contaminated_selection"),
        ("2026-11-01", "forward"),
        ("2026-11-30", "forward"),
    ]
    got = TG._classify([pd.Timestamp(d) for d, _ in cases])
    for (d, want), g in zip(cases, got):
        check("%s → %s" % (d, want), g == want, "got %s" % g)
    check("NaT → unknown", TG._classify([pd.NaT])[0] == "unknown")


def test_columns():
    print("[T2] 两个 CSV 都有 caliber 列且行数不变")
    for path, bak, label in ((LIVE, LIVE_BAK, "43f"), (SHADOW, SHADOW_BAK, "21f")):
        if not os.path.isfile(path):
            check("%s 存在" % label, False, path)
            continue
        cur = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        check("%s 有 caliber 列" % label, "caliber" in cur.columns, str(list(cur.columns)))
        if "caliber" in cur.columns:
            vals = set(cur["caliber"].dropna().unique())
            check("%s caliber 取值合法" % label,
                  vals.issubset({"clean", "contaminated_selection", "forward"}),
                  str(vals))
            check("%s 无 NaN caliber" % label, int(cur["caliber"].isna().sum()) == 0,
                  "%d 行 NaN" % int(cur["caliber"].isna().sum()))
        if os.path.isfile(bak):
            old = pd.read_csv(bak, encoding="utf-8-sig", low_memory=False)
            check("%s 行数未变（%d）" % (label, len(old)), len(cur) == len(old),
                  "%d → %d" % (len(old), len(cur)))


def test_returns_unchanged():
    print("[T4] 收益数值未被改动（与备份逐笔比对）")
    for path, bak, label, retcol in ((LIVE, LIVE_BAK, "43f", "ret"),
                                     (SHADOW, SHADOW_BAK, "21f", "ret10")):
        if not (os.path.isfile(path) and os.path.isfile(bak)):
            print("  SKIP  %s（缺文件）" % label)
            continue
        cur = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        old = pd.read_csv(bak, encoding="utf-8-sig", low_memory=False)
        if retcol not in cur.columns or retcol not in old.columns:
            print("  SKIP  %s 无 %s 列" % (label, retcol))
            continue
        a = pd.to_numeric(old[retcol], errors="coerce").to_numpy()
        b = pd.to_numeric(cur[retcol], errors="coerce").to_numpy()
        same = len(a) == len(b) and np.allclose(a, b, equal_nan=True, atol=0)
        check("%s 的 %s 逐笔完全一致" % (label, retcol), same,
              "len %d/%d 或值有差异" % (len(a), len(b)))


def test_consumers_filter():
    print("[T3] 消费端确实排除 contaminated_selection")
    consumers = [
        "forward_stats.py",
        "paper_forward_downgrade.py",
        "paper_forward_exit.py",
        "paper_quarterly_review.py",
        "rollback_check_g2.py",
        "paper_forward_ab_stats.py",
    ]
    for f in consumers:
        p = os.path.join(HERE, f)
        if not os.path.isfile(p):
            check("%s 存在" % f, False)
            continue
        src = open(p, encoding="utf-8").read()
        ok = ('caliber' in src) and ('contaminated_selection' in src)
        check("%s 过滤 contaminated_selection" % f, ok)


def test_writer_defaults_forward():
    print("[T5] 写入侧新记录默认 caliber=forward")
    src = open(os.path.join(HERE, "paper_forward.py"), encoding="utf-8").read()
    check("paper_forward 追加时补 caliber=forward",
          'new["caliber"] = "forward"' in src)
    src2 = open(os.path.join(HERE, "paper_shadow_b21.py"), encoding="utf-8").read()
    check("paper_shadow_b21 合并时补 caliber",
          'picks_all["caliber"]' in src2)
    check("PICK_COLS 含 caliber", '"caliber"' in src2)


def main():
    try:
        test_classify()
        test_columns()
        test_returns_unchanged()
        test_consumers_filter()
        test_writer_defaults_forward()
    except Exception:
        import traceback
        traceback.print_exc()
        FAILS.append("EXCEPTION")
    print("")
    if FAILS:
        print("FAILED: %d 项 → %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
