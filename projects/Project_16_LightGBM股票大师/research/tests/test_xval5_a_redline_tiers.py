# -*- coding: utf-8 -*-
"""XVAL5 工作包 A 回归测试（T-20260929-XVAL3）。

断言修复后（补上 deploy_predict_g2.py L237-264 新浪兜底步）：
  1. 2026-09-28 Top100 红线各档原始计数恢复 1/4/12/21/33（thr 60/58/55/52/50）
  2. F5 中性化 60 档 = 5（五只名单与 XVAL_JOINT §三 一致）
  3. 生产 CSV 10 列不变、值不变（000607.SZ 61.5 / 8/6/9/3/2/5）
  4. OP 复刻口径（无兜底）仍复现 1/3/11/20/32 —— 锁定 bug 形态防回归
  5. 任务书前提「三只 58.5 同分票之一塌分至 <50」为伪：920651.BJ 塌至 50.5（非 <50），
     50 档 -1 由 920274.BJ（50.5→42.5）贡献
  6. 维度级：两路径仅 SC_F2 存在差异，且仅 920651.BJ / 920274.BJ 两票

运行：python research/tests/test_xval5_a_redline_tiers.py
      或 pytest research/tests/test_xval5_a_redline_tiers.py
离线：新浪兜底值取 research/xval5_a_sina_20260928.json（xval5_a_repro.py 实时拉取后落盘，
      并已与 xval_de_p1_top100_20260928.csv 及 2026-09-29 新浪接口回查三方核对一致）。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESEARCH = os.path.dirname(HERE)
PROJ = os.path.dirname(RESEARCH)
sys.path.insert(0, PROJ)
sys.path.insert(0, RESEARCH)

from xval5_a_repro import (SINA_JSON, PROD_CSV, THRS, run, tier_counts)

EXPECT_FIX = {60.0: 1, 58.0: 4, 55.0: 12, 52.0: 21, 50.0: 33}
EXPECT_OP = {60.0: 1, 58.0: 3, 55.0: 11, 52.0: 20, 50.0: 32}
NEU60_EXPECT_CODES = {"000607.SZ", "000786.SZ", "001332.SZ", "920651.BJ", "600876.SH"}

_RES = {}


def _res():
    if "op" not in _RES:
        sina_map = json.load(open(SINA_JSON, encoding="utf-8"))["codes"]
        res_op, res_fix, _meta = run(fetch_live=False, sina_map=sina_map, verbose=False)
        _RES["op"], _RES["fix"] = res_op, res_fix
    return _RES["op"], _RES["fix"]


def test_fix_tier_counts_restored():
    """断言1：修复后原始计数 = 1/4/12/21/33。"""
    _, res_fix = _res()
    got = tier_counts(res_fix)
    assert got == EXPECT_FIX, "FIX 各档计数 %s != 期望 %s" % (got, EXPECT_FIX)


def test_op_tier_counts_reproduce_bug():
    """断言4：无兜底路径仍复现 OP 错误计数 1/3/11/20/32（锁定 bug 形态）。"""
    res_op, _ = _res()
    got = tier_counts(res_op)
    assert got == EXPECT_OP, "OP 各档计数 %s != 期望 %s" % (got, EXPECT_OP)


def test_neutralized_60_is_5():
    """断言2：FIX 路径 F5 中性化 60 档 = 5，名单与联合裁决一致。"""
    _, res_fix = _res()
    neu = res_fix["total_new"] + 0.10 * (5.0 - res_fix["SC_F5"]) * 10.0
    passed = set(res_fix.loc[neu >= 60.0, "ts_code"])
    assert len(passed) == 5, "中性化 60 档 = %d != 5" % len(passed)
    assert passed == NEU60_EXPECT_CODES, "名单 %s != %s" % (passed, NEU60_EXPECT_CODES)


def test_production_csv_untouched():
    """断言3：生产 CSV 10 列 1 行、000607.SZ 值不变；FIX 重算逐格一致。"""
    prod = pd.read_csv(PROD_CSV, encoding="utf-8-sig", dtype={"ts_code": str})
    assert list(prod.columns) == ["ts_code", "prob", "total_new", "SC_F1", "SC_F2",
                                  "SC_F3", "SC_F4", "SC_F5", "SC_F6", "trade_date"]
    assert len(prod) == 1
    r = prod.iloc[0]
    assert r["ts_code"] == "000607.SZ"
    assert abs(float(r["prob"]) - 0.6541) < 1e-9
    assert abs(float(r["total_new"]) - 61.5) < 1e-9
    for c, v in [("SC_F1", 8.0), ("SC_F2", 6.0), ("SC_F3", 9.0),
                 ("SC_F4", 3.0), ("SC_F5", 2.0), ("SC_F6", 5.0)]:
        assert abs(float(r[c]) - v) < 1e-9, "%s=%s != %s" % (c, r[c], v)
    # FIX 重算与 CSV 逐格一致
    _, res_fix = _res()
    picks = res_fix[res_fix["total_new"] >= 60.0].nlargest(10, "total_new")
    assert list(picks["ts_code"]) == ["000607.SZ"]
    p = picks.iloc[0]
    assert abs(float(p["total_new"]) - 61.5) < 1e-9
    for c, v in [("SC_F1", 8.0), ("SC_F2", 6.0), ("SC_F3", 9.0),
                 ("SC_F4", 3.0), ("SC_F5", 2.0), ("SC_F6", 5.0)]:
        assert abs(float(p[c]) - v) < 1e-9


def test_no_tied_ticket_below_50():
    """断言5：三只 58.5 同分票无一只塌至 <50；920651.BJ 塌至 50.5；
    50 档 -1 由 920274.BJ（50.5→42.5）贡献。"""
    res_op, res_fix = _res()
    tied = ["000786.SZ", "920651.BJ", "001332.SZ"]
    for t in tied:
        f = float(res_fix.loc[res_fix["ts_code"] == t, "total_new"].iloc[0])
        assert abs(f - 58.5) < 1e-9, "FIX %s total=%.1f != 58.5" % (t, f)
    for t in ("000786.SZ", "001332.SZ"):
        o = float(res_op.loc[res_op["ts_code"] == t, "total_new"].iloc[0])
        assert abs(o - 58.5) < 1e-9, "OP %s total=%.1f != 58.5（不应受影响）" % (t, o)
    o651 = float(res_op.loc[res_op["ts_code"] == "920651.BJ", "total_new"].iloc[0])
    assert abs(o651 - 50.5) < 1e-9, "OP 920651.BJ total=%.1f != 50.5" % o651
    assert o651 >= 50.0, "任务书前提（同分票塌至 <50）为伪：920651.BJ 塌至 50.5，未破 50"
    f274 = float(res_fix.loc[res_fix["ts_code"] == "920274.BJ", "total_new"].iloc[0])
    o274 = float(res_op.loc[res_op["ts_code"] == "920274.BJ", "total_new"].iloc[0])
    assert abs(f274 - 50.5) < 1e-9 and abs(o274 - 42.5) < 1e-9, \
        "920274.BJ 应为 FIX=50.5 / OP=42.5，实得 %.1f / %.1f" % (f274, o274)


def test_only_f2_only_two_tickets_differ():
    """断言6：两路径仅 SC_F2 差异、仅 920651.BJ/920274.BJ 两票（F1/F3/F4/F5/F6 全等）。"""
    res_op, res_fix = _res()
    assert (res_fix["ts_code"].values == res_op["ts_code"].values).all()
    two = {"920651.BJ", "920274.BJ"}
    for c in ["SC_F1", "SC_F3", "SC_F4", "SC_F5", "SC_F6"]:
        d = np.abs(res_fix[c].astype(float).values - res_op[c].astype(float).values)
        bad = set(res_fix["ts_code"].values[d > 1e-9])
        assert not bad, "%s 出现非预期差异票 %s" % (c, bad)
    for c in ["SC_F2", "total_new"]:  # F2 差异按 W2*10 传导至 total_new，两票属预期
        d = np.abs(res_fix[c].astype(float).values - res_op[c].astype(float).values)
        bad = set(res_fix["ts_code"].values[d > 1e-9])
        assert bad == two, "%s 差异票 %s != 两票 %s" % (c, bad, two)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    n_fail = 0
    for fn in fns:
        try:
            fn()
            print("PASS  %s" % fn.__name__)
        except AssertionError as e:
            n_fail += 1
            print("FAIL  %s: %s" % (fn.__name__, e))
    print("\n%d/%d passed" % (len(fns) - n_fail, len(fns)))
    sys.exit(1 if n_fail else 0)
