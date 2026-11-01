# -*- coding: utf-8 -*-
"""_test_v13_paper_parallel_guard.py — v13_paper_parallel 取数口径 / new臂新鲜度闸 回归。

背景（2026-11-01）：
  T1  记录闸原只看 data_live/incremental_daily.parquet。xtdata_update 自 10-26 连不上行情
      服务 → 增量库冻结 10-23，主库 D:/astock 却每日由 Quant_Astock_DailyTushare_1740 补到当日
      ⇒ 记录被整晚跳过（10-26~10-30 双臂 0 条）、report 每晚误判 stale exit 2。
  T2  补录时 new 臂 `D = max(final_list < T)` 无新鲜度闸：10-24~10-29 无 final_list 时
      D 恒为 10-23，同 3 只票被记成 5 个独立信号（虚增样本、收益重复）。

本测试全部在临时目录 + 桩数据上跑，不触碰生产 log / 选股产物。

用法：python _test_v13_paper_parallel_guard.py     （0=全过，1=有失败）
"""
import os
import shutil
import sys
import tempfile
import traceback

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import v13_paper_parallel as V  # noqa: E402

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s %s" % (name, detail))
        FAILS.append(name)


def write_final_list(path, rows):
    df = pd.DataFrame(rows, columns=["ts_code", "total", "redline_pass", "model_prob"])
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_selection_full(path, rows):
    df = pd.DataFrame(rows, columns=["ts_code", "total", "model_prob"])
    df.to_csv(path, index=False, encoding="utf-8-sig")


def read_log():
    return pd.read_csv(V.LOG_CSV, encoding="utf-8-sig", dtype={"ts_code": str})


def setup_tmp(tmp):
    V.SELECT_DIR = os.path.join(tmp, "selections")
    V.LOG_CSV = os.path.join(tmp, "log.csv")
    V.REPORT_MD = os.path.join(tmp, "report.md")
    os.makedirs(V.SELECT_DIR, exist_ok=True)
    # 交易日历桩：10-23(五) → 10-26(一) → 10-27 → 10-28 → 10-29 → 10-30
    V._TRADING_DAYS = [pd.Timestamp(d) for d in
                       ("2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27",
                        "2026-10-28", "2026-10-29", "2026-10-30")]


def test_prev_trading_day():
    print("[T1] _prev_trading_day 跨周末")
    check("10-26 的上一交易日 = 10-23",
          V._prev_trading_day(pd.Timestamp("2026-10-26")) == pd.Timestamp("2026-10-23"))
    check("10-27 的上一交易日 = 10-26",
          V._prev_trading_day(pd.Timestamp("2026-10-27")) == pd.Timestamp("2026-10-26"))
    check("日历首日之前返回 None",
          V._prev_trading_day(pd.Timestamp("2026-10-22")) is None)


def test_stale_guard():
    print("[T2] new臂新鲜度闸：final_list 断档不复用")
    write_final_list(os.path.join(V.SELECT_DIR, "20261023_final_list.csv"),
                     [["300452.SZ", 60.5, "True", 0.71],
                      ["301200.SZ", 59.5, "True", 0.70],
                      ["300604.SZ", 58.5, "True", 0.69]])
    write_selection_full(os.path.join(V.SELECT_DIR, "20261028_selection_full.csv"),
                         [["600000.SH", 66.0, 0.80]])
    V.record(pd.Timestamp("2026-10-28"))
    df = read_log()
    new = df[df["arm"] == "new"]
    check("过期日只落 1 条哨兵行", len(new) == 1, "got %d" % len(new))
    check("哨兵为 __NOFILE__", str(new.iloc[0]["ts_code"]) == V.NOFILE, str(new.iloc[0]["ts_code"]))
    check("note 写明信号过期", "信号过期" in str(new.iloc[0]["note"]), str(new.iloc[0]["note"]))
    check("不复用旧票", not new["ts_code"].isin(["300452.SZ", "301200.SZ", "300604.SZ"]).any())
    old = df[df["arm"] == "old"]
    check("old臂照常记录", len(old) == 1 and str(old.iloc[0]["ts_code"]) == "600000.SH")


def test_fresh_ok():
    print("[T3] final_list = 上一交易日 → 正常记录")
    write_final_list(os.path.join(V.SELECT_DIR, "20261027_final_list.csv"),
                     [["000001.SZ", 62.0, "True", 0.75]])
    write_selection_full(os.path.join(V.SELECT_DIR, "20261028_selection_full.csv"),
                         [["600000.SH", 66.0, 0.80]])
    V.record(pd.Timestamp("2026-10-28"))
    df = read_log()
    new = df[(df["arm"] == "new") & (df["exec_date"] == "2026-10-28")]
    real = new[new["ts_code"] != V.NOFILE]
    check("新鲜信号落真实票（不再判过期）", len(real) == 1, "got %d" % len(real))
    check("票号正确", str(real.iloc[0]["ts_code"]) == "000001.SZ", str(real.iloc[0]["ts_code"]))
    check("signal_date = 上一交易日 10-27",
          str(real.iloc[0]["signal_date"]) == "2026-10-27", str(real.iloc[0]["signal_date"]))
    check("红线过滤生效（total 62 >= 58，rank=1）",
          int(real.iloc[0]["rank"]) == 1 and float(real.iloc[0]["total"]) == 62.0)
    # 说明：T2 已写入同执行日的 __NOFILE__ 哨兵，append_dedup 按
    # (exec_date, arm, ts_code) 去重，哨兵与真实票是不同 ts_code ⇒ 两者并存。
    # 这是**预期**行为：哨兵记录的是「当时确实取不到信号」这一历史事实，
    # 后续补录到新鲜产物时不应抹掉它。报表侧按 ts_code 是否为哨兵区分统计。


def test_weekend_pairing():
    print("[T4] 跨周末配对：10-23 产物 → 10-26 执行日（合法，不算过期）")
    write_selection_full(os.path.join(V.SELECT_DIR, "20261026_selection_full.csv"),
                         [["600519.SH", 61.0, 0.7]])
    V.record(pd.Timestamp("2026-10-26"))
    df = read_log()
    new = df[(df["arm"] == "new") & (df["exec_date"] == "2026-10-26")]
    check("10-26 记录 3 只（signal_date=10-23）", len(new) == 3, "got %d" % len(new))
    check("signal_date 均为 10-23", set(new["signal_date"]) == {"2026-10-23"}, str(set(new["signal_date"])))
    check("无过期哨兵", not new["ts_code"].isin([V.NOFILE]).any())


def test_latest_panel_date():
    print("[T5] _latest_panel_date 取主库/增量最大值")
    idx = pd.MultiIndex.from_tuples(
        [(pd.Timestamp("2026-10-29"), "600000.SH"), (pd.Timestamp("2026-10-30"), "600000.SH")],
        names=["trade_date", "ts_code"])
    orig_read = V.DC.read_main_daily
    orig_incr = V.INCR
    try:
        V.DC.read_main_daily = lambda columns=None: pd.DataFrame({"close": [1.0, 1.1]}, index=idx)
        V.INCR = os.path.join(os.path.dirname(V.LOG_CSV), "no_such_incr.parquet")
        T, main_last, inc_last = V._latest_panel_date()
        check("增量缺席不阻断，基准=主库 10-30", T == pd.Timestamp("2026-10-30"), str(T))
        check("inc_last 为 None", inc_last is None)

        V.DC.read_main_daily = lambda columns=None: pd.DataFrame(
            {"close": [1.0]}, index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-10-23"), "600000.SH")], names=["trade_date", "ts_code"]))
        inc_path = os.path.join(os.path.dirname(V.LOG_CSV), "inc.parquet")
        pd.DataFrame({"trade_date": [pd.Timestamp("2026-10-30")], "ts_code": ["600000.SH"],
                      "close": [9.9]}).to_parquet(inc_path, index=False)
        V.INCR = inc_path
        T2, m2, i2 = V._latest_panel_date()
        check("增量更新 → 取增量 10-30", T2 == pd.Timestamp("2026-10-30"), str(T2))
        check("main_last 仍为 10-23", m2 == pd.Timestamp("2026-10-23"), str(m2))

        def _boom(columns=None):
            raise IOError("boom")
        V.DC.read_main_daily = _boom
        V.INCR = os.path.join(os.path.dirname(V.LOG_CSV), "nope.parquet")
        T3, m3, i3 = V._latest_panel_date()
        check("两边都不可读 → None（调用方 FAIL-LOUD）", T3 is None)
    finally:
        V.DC.read_main_daily = orig_read
        V.INCR = orig_incr


def test_dedup():
    print("[T6] append_dedup 幂等")
    rows = [{"exec_date": "2026-10-28", "arm": "old", "signal_date": "2026-10-28",
             "ts_code": "600000.SH", "rank": 1, "total": 66.0, "model_prob": 0.8,
             "fillable": True, "entry": np.nan, "exit": np.nan, "ret": np.nan,
             "ret_1d": np.nan, "note": ""}]
    before = len(read_log())
    V.append_dedup(rows)
    V.append_dedup(rows)
    after = len(read_log())
    check("重复追加不增长", before == after, "%d -> %d" % (before, after))


def main():
    tmp = tempfile.mkdtemp(prefix="v13pp_")
    try:
        setup_tmp(tmp)
        test_prev_trading_day()
        test_stale_guard()
        test_fresh_ok()
        test_weekend_pairing()
        test_latest_panel_date()
        test_dedup()
    except Exception:
        traceback.print_exc()
        FAILS.append("EXCEPTION")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("")
    if FAILS:
        print("FAILED: %d 项 → %s" % (len(FAILS), ", ".join(FAILS)))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
