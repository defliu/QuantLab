# coding=utf-8
"""review_full 日期锚 fail-closed 单元测试（T-20261009-004）。

背景（2026-10-09 事故）：国庆假期后 09:45 任务沿用/误写旧 tdx_review.json
（date=20260930）→ review_full 照单全收 → 产物写成 20260930_selection_full.csv
→ rebalance_daily 找 20261009 清单落空 → V1.x 静默空仓一整天（P0 断流）。
修复：_resolve_rdate —— JSON 日期非今日且无显式 --date 时 exit 2 拒绝生成。

覆盖 review_full._resolve_rdate：
  R1 旧 JSON 日期 + 无 --date   → SystemExit(2) fail-closed
  R2 旧 JSON 日期 + 显式 --date → 按指示返回（复盘/补跑场景合法）
  R3 JSON 日期=今日             → 返回今日
  R4 JSON 无日期字段            → 回退今日
  R5 --date=今日 + JSON 旧日期  → 今日（显式优先，不误伤）

运行（miniqmt venv）：
  cd D:/QuantLab/projects/Project_16_LightGBM股票大师
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_review_rdate_guard.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

import review_full  # noqa: E402

PASS = 0
FAIL = 0


def _check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s  %s" % (name, detail))


def _expect_exit2(args_date, review_date):
    try:
        review_full._resolve_rdate(args_date, review_date)
    except SystemExit as e:
        return e.code
    return None


def test_r1_stale_json_fail_closed():
    """R1 旧 JSON 日期 + 无 --date → SystemExit 且码=2（绝不错日生成）。"""
    code = _expect_exit2(None, "20200101")
    _check("R1 旧 JSON 日期无 --date → SystemExit(2) fail-closed", code == 2, code)


def test_r2_explicit_date_override():
    """R2 显式 --date 覆盖 JSON 日期（复盘/补跑合法路径，不 exit）。"""
    r = review_full._resolve_rdate("20200101", "20260930")
    _check("R2 显式 --date 覆盖 JSON 日期", r == "20200101", r)


def test_r3_json_today():
    """R3 JSON 日期=今日 → 正常返回今日。"""
    today = time.strftime("%Y%m%d")
    r = review_full._resolve_rdate(None, today)
    _check("R3 JSON 日期=今日 → 返回今日", r == today, r)


def test_r4_json_missing_date():
    """R4 JSON 无日期字段 → 回退今日墙钟。"""
    today = time.strftime("%Y%m%d")
    r = review_full._resolve_rdate(None, None)
    _check("R4 JSON 无日期 → 回退今日", r == today, r)


def test_r5_args_today_overrides_stale_json():
    """R5 --date=今日 + JSON 旧日期 → 显式优先返回今日（guard 补跑路径，不误伤）。"""
    today = time.strftime("%Y%m%d")
    r = review_full._resolve_rdate(today, "20200101")
    _check("R5 --date=今日 覆盖旧 JSON 日期", r == today, r)


if __name__ == "__main__":
    print("== review_full 日期锚 fail-closed 测试（T-20261009-004）==")
    test_r1_stale_json_fail_closed()
    test_r2_explicit_date_override()
    test_r3_json_today()
    test_r4_json_missing_date()
    test_r5_args_today_overrides_stale_json()
    print("\nPASS=%d FAIL=%d" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)
