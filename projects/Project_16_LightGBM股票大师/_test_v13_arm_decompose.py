# -*- coding: utf-8 -*-
"""_test_v13_arm_decompose.py — v13_arm_decompose 口径隔离回归。

背景（2026-11-01）：
  「哪个臂好」的归因曾把三个变量混在一起（信号时点 / 池构造 / 评分卡源）。
  复核后澄清两件事，本测试把它们钉住，防止再被误判：

  T1  **两臂用同一面板日**：`_next_td(D)` 给出的执行日 T 与 D 之间恰好隔一个交易日，
      且 D 本身不是执行日 —— 即信号日 = T 的前一交易日（不存在「旧臂用当日盘中」）。
  T2  **池构造差异可复现**：给定同一面板日，`纯 prob Top10` 与
      `deploy_predict 落盘池` 的交集应显著小于 10（证明两池不同源）。
  T3  **红线/Top5 规则一致**：`_pick` 对同一份评分结果，两臂都用 58 / Top5。
  T4  **评分卡行序对齐**：`_score` 对乱序 pool 仍给出与 ts_code 对应的分数
      （防 `compute_real_scorecard` 的 `.values` 错位）。
  T5  **历史不可重放的护栏**：脚本顶部必须声明「面板重建 + 快照覆盖 ⇒ 不可回放」，
      且产物报告必须含该声明（防止未来有人拿 P_* 当历史事实）。

全部离线，不写生产产物、不触碰 log。

用法：python _test_v13_arm_decompose.py   （0=全过，1=有失败）
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v13_arm_decompose as AD  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        FAILS.append(name)


def test_next_td_is_prev_of_exec():
    print("[T1] 执行日 T = D 的下一交易日（两臂同一面板日）")
    trades = AD._load_panel_open()
    for d in ("2026-10-19", "2026-10-20", "2026-10-22", "2026-10-23"):
        D = pd.Timestamp(d)
        T = AD._next_td(D)
        check("%s 有下一交易日" % d, T is not None)
        if T is None:
            continue
        # 信号日必须早于执行日（D 是交易日，但不是执行日）
        check("%s 早于执行日 %s" % (d, T.date()), D < T)
        # T 与 D 之间没有其他交易日
        between = [t for t in trades if D < t < T]
        check("%s→%s 之间无其他交易日" % (d, T.date()), len(between) == 0, str(between))


def test_pool_differs():
    print("[T2] 两臂候选池不同源（池构造差异可复现）")
    panel, feat, booster = AD._panel_and_model()
    checked = 0
    for d in ("2026-10-19", "2026-10-20", "2026-10-22"):
        D = pd.Timestamp(d)
        mtp = os.path.join(AD.SELECT_DIR, "%s_model_top10.csv" % D.strftime("%Y%m%d"))
        if not os.path.isfile(mtp):
            continue
        day = panel[panel["trade_date"] == D].copy()
        if len(day) == 0:
            continue
        day["model_prob"] = booster.predict(day[feat].astype("float32").values)
        pure = set(day.nlargest(AD.BV.POOL_N, "model_prob")["ts_code"])
        mt = pd.read_csv(mtp, encoding="utf-8-sig", dtype={"ts_code": str})
        old = set(mt["ts_code"].astype(str).str.strip())
        ovl = len(pure & old)
        check("%s 两池交集 %d/10（应 <10）" % (d, ovl), ovl < AD.BV.POOL_N)
        checked += 1
    check("至少检查到 1 个面板日", checked >= 1, "checked=%d" % checked)


def test_pick_rules():
    print("[T3] 红线 58 / Top5 规则一致")
    df = pd.DataFrame({
        "ts_code": ["A", "B", "C", "D", "E", "F", "G"],
        "model_prob": [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3],
        "total": [70.0, 66.0, 60.0, 58.0, 57.5, 55.0, 54.0],
    })
    picked = AD._pick(df)
    check("红线 57.5 被剔除", "E" not in set(picked["ts_code"]))
    check("红线 58.0 保留", "D" in set(picked["ts_code"]))
    check("Top5 上限", len(picked) <= 5, "got %d" % len(picked))
    check("按 total 降序", list(picked["total"]) == sorted(picked["total"], reverse=True))
    check("常量 REDLINE=58 / TOP_N=5", AD.REDLINE == 58.0 and AD.TOP_N == 5)


def test_score_row_alignment():
    print("[T4] 评分卡行序对齐（乱序 pool 不错位）")
    mf = {"B": 2e7, "A": 0.0}
    ind = {"B": 3.0, "A": -2.0}
    db = pd.DataFrame({"pe_ttm": [15.0, 25.0], "turnover_rate": [2.0, 1.0]}, index=["B", "A"])
    # 评分卡需要真实特征列（pos_250/rsi6/macd_hist/rel_mom_20/...），用真实面板两行做桩
    panel, _, _ = AD._panel_and_model()
    d = panel["trade_date"].max()
    day = panel[panel["trade_date"] == d].head(2).reset_index(drop=True)
    codes = list(day["ts_code"])
    pool = day[["ts_code", "pos_250", "rsi6", "macd_hist", "rel_mom_20",
                "volume_ratio", "pe_ttm", "turn_ma5"]].copy()
    pool["model_prob"] = 0.5
    mf = {codes[0]: 2e7, codes[1]: 0.0}
    ind = {codes[0]: 3.0, codes[1]: -2.0}
    db = pd.DataFrame({"pe_ttm": [15.0, 25.0], "turnover_rate": [2.0, 1.0]}, index=codes)
    sc = AD._score(pool, mf, ind, db)
    f2_a = float(sc.loc[sc["ts_code"] == codes[0], "F2"].iloc[0])
    f2_b = float(sc.loc[sc["ts_code"] == codes[1], "F2"].iloc[0])
    check("净流入方 F2 更高（资金面正确挂到行上）", f2_a > f2_b, "%.1f vs %.1f" % (f2_a, f2_b))
    f5_a = float(sc.loc[sc["ts_code"] == codes[0], "F5"].iloc[0])
    f5_b = float(sc.loc[sc["ts_code"] == codes[1], "F5"].iloc[0])
    check("行业涨方 F5 更高（行业面正确挂到行上）", f5_a > f5_b, "%.1f vs %.1f" % (f5_a, f5_b))
    check("ts_code 未被改动", list(sc["ts_code"]) == codes, str(list(sc["ts_code"])))


def test_irreplayable_disclosure():
    print("[T5] 「历史不可重放」声明存在（防未来把 P_* 当历史事实）")
    src = open(os.path.abspath(AD.__file__), encoding="utf-8").read()
    for kw in ("不可重放", "重建", "快照"):
        check("脚本含关键词「%s」" % kw, kw in src)
    md_path = AD.OUT_MD
    if os.path.isfile(md_path):
        md = open(md_path, encoding="utf-8").read()
        check("报告含「历史不可重放」声明", "不可重放" in md)
        check("报告含「只能定性」约束", "只能定性" in md)
    else:
        print("  SKIP  报告尚未生成（%s）" % os.path.basename(md_path))


def main():
    try:
        test_next_td_is_prev_of_exec()
        test_pool_differs()
        test_pick_rules()
        test_score_row_alignment()
        test_irreplayable_disclosure()
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
