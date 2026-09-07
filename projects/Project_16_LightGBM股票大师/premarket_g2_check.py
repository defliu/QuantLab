# coding: utf-8
"""G2 盘前核对（2026-09-02 P0 修复后新增，2026-09-03 加数据当日性检查）。
四项检查：
  1) 心跳 build_tag 是否已切换为新版（QMT 界面重载生效判据）
  2) 当日候选文件是否存在（rebalance 换仓输入）
  3) 昨日超额持仓提醒（600262 6400 / 300964 1000 vs 账本 3000/800）
  4) 数据当日性：增量库最新交易日 + 快照 trade_date + F6 估值当日性（2026-09-03 加，防估值再滞后）

用法:
  python premarket_g2_check.py                # 自动取今天
  python premarket_g2_check.py --date 20260903
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data_config as DC
import g2_config as G
from qmt_bridge_client import read_heart, read_positions

EXPECTED_BUILD_TAG = "20260902-161814"

INCR_PATH = os.path.join(DC.LIVE_DIR, "incremental_daily.parquet")
SNAP_PATH = os.path.join(DC.LIVE_DIR, "g2_latest_features.parquet")

# F6 反推校验抽样（估值字段须反映当日 close 而非主库周更旧值）
F6_SAMPLE_CODES = ["600262.SH", "300964.SZ", "000001.SZ", "600519.SH", "000858.SZ"]

OVERSHOOT_WATCH = {
    "600262.SH": {"ledger": 3000, "actual": 6400},
    "300964.SZ": {"ledger": 800, "actual": 1000},
}


def _latest_heart():
    """读 state 目录最新的 heart_<date>.json（桥每天写新心跳，不限于 date 参数）。"""
    try:
        files = [f for f in os.listdir(G.STATE_DIR) if f.startswith("heart_") and f.endswith(".json")]
        if not files:
            return None, None
        files.sort(reverse=True)  # YYYYMMDD 字典序 = 日期序
        latest = os.path.join(G.STATE_DIR, files[0])
        with open(latest, encoding="utf-8") as f:
            return files[0], json.load(f)
    except Exception:
        return None, None


def check_heartbeat(date):
    print("=" * 60)
    print("[1] 心跳 build_tag 校验（部署生效判据）")
    print("-" * 60)
    fname, heart = _latest_heart()
    if not heart:
        print("  !! 心跳文件缺失/为空，桥可能未运行")
        return False
    bt = heart.get("build_tag", "")
    ok = (bt == EXPECTED_BUILD_TAG)
    print("  心跳文件=%s  date=%s last_heartbeat=%s" % (fname, heart.get("date", ""), heart.get("last_heartbeat", "")))
    print("  build_tag=%s  期望=%s  %s" % (bt, EXPECTED_BUILD_TAG, "OK" if ok else "!! 未切换新版，P0 修复未生效"))
    print("  pending=%d last_cmd_seq=%d" % (heart.get("pending_count", 0), heart.get("last_cmd_seq_processed", 0)))
    return ok


def check_candidate(date):
    print("=" * 60)
    print("[2] 候选文件就绪（rebalance 换仓输入，Top10 池）")
    print("-" * 60)
    path = os.path.join(G.G2_SELECT_DIR, "%s_g2_top%d.csv" % (date, G.SELECT_TOP))
    if not os.path.exists(path):
        print("  !! 候选缺失: %s" % path)
        print("     需先跑 09:25 任务：build_g2_daily.py + deploy_predict_g2.py --top 10")
        return False
    st = os.stat(path)
    print("  OK 候选存在: %s (%.0fB, %s)" % (os.path.basename(path), st.st_size, time.strftime("%H:%M:%S", time.localtime(st.st_mtime))))
    with open(path, encoding="utf-8-sig") as f:
        import csv as _csv
        for row in _csv.DictReader(f):
            print("    - %s total=%s" % (row.get("ts_code", ""), row.get("total_new", row.get("total", ""))))
    return True


def check_overshoot(date):
    print("=" * 60)
    print("[3] 昨日超额持仓提醒（2026-09-02 P0 遗留）")
    print("-" * 60)
    pos = read_positions(date) or {}
    pos_map = {p.get("code", ""): p for p in pos.get("positions", [])}
    for code, exp in OVERSHOOT_WATCH.items():
        p = pos_map.get(code)
        if p is None:
            print("  ? %s 未在 positions 快照中" % code)
            continue
        actual = p.get("volume", 0)
        extra = actual - exp["ledger"]
        print("  %s 实持=%d 账本=%d 超额=%d %s" % (
            code, actual, exp["ledger"], extra,
            "（需明日换仓核对差额）" if extra > 0 else ""))
    print("-" * 60)
    print("  注：账本与实持不一致时，换仓/对账以 positions 实际持仓为准。")


def _check_f6_live(snap_latest):
    """F6 估值当日性：主库仍滞后于快照时，抽样验证快照 pe_ttm = 主库每股EPS × 当日close 反推。
    主库已到快照日（周更后）则估值即主库当日值，直接通过。"""
    try:
        main = pd.read_parquet(DC.MAIN_DAILY, columns=["ts_code", "trade_date", "close", "pe_ttm"]).reset_index()
        main["trade_date"] = pd.to_datetime(main["trade_date"])
        m_last_dt = main["trade_date"].max()
        m_last_s = pd.Timestamp(m_last_dt).strftime("%Y%m%d")
        if m_last_s >= snap_latest:
            print("  主库已到 %s >= 快照 %s：估值即主库当日值，无需反推校验" % (m_last_s, snap_latest))
            return True
        m = main[main["trade_date"] == m_last_dt].set_index("ts_code")
        incr = pd.read_parquet(INCR_PATH)
        i9 = incr[incr["trade_date"] == incr["trade_date"].max()].set_index("ts_code")
        snap = pd.read_parquet(SNAP_PATH)
        snap = snap[snap["trade_date"] == snap["trade_date"].max()].set_index("ts_code")
        eps = (m["close"] / m["pe_ttm"].replace(0, np.nan))
        ok = True
        n = 0
        for c in F6_SAMPLE_CODES:
            if c not in i9.index or c not in snap.index or c not in m.index:
                continue
            close9 = float(i9.loc[c, "close"])
            if not np.isfinite(eps[c]) or not np.isfinite(close9):
                continue
            pe_calc = close9 / eps[c]
            pe_snap = float(snap.loc[c, "pe_ttm"])
            good = abs(pe_calc - pe_snap) < 0.5
            ok = ok and good
            n += 1
            print("  %s 反推pe=%.2f vs 快照=%.2f %s" % (c, pe_calc, pe_snap, "OK" if good else "!! 不一致"))
        if n == 0:
            print("  !! 抽样股票均不可比（主库/增量/快照缺 code），无法校验 F6")
            return False
        print("  F6 估值当日性: %s（抽样 %d 只，pe_ttm 必须=当日close×主库EPS 反推）" % ("PASS" if ok else "FAIL", n))
        return ok
    except Exception as e:
        print("  !! F6 校验异常: %s" % e)
        return False


def check_data_freshness(date):
    print("=" * 60)
    print("[4] 数据当日性（增量库 + 快照 + F6 估值）")
    print("-" * 60)
    # a) 增量库最新交易日 == 期望数据日
    try:
        incr = pd.read_parquet(INCR_PATH, columns=["trade_date"])
        incr_latest = pd.Timestamp(incr["trade_date"].max()).strftime("%Y%m%d")
    except Exception as e:
        print("  !! 增量库读取失败: %s" % e)
        return False
    ok_incr = (incr_latest == date)
    print("  增量库最新交易日=%s  期望=%s  %s" % (incr_latest, date, "OK" if ok_incr else "!! 增量未到当日，先跑 xtdata_update"))
    # b) 快照 trade_date == 期望数据日
    try:
        snap = pd.read_parquet(SNAP_PATH, columns=["trade_date"])
        snap_latest = pd.Timestamp(snap["trade_date"].max()).strftime("%Y%m%d")
    except Exception as e:
        print("  !! 快照读取失败: %s" % e)
        return False
    ok_snap = (snap_latest == date)
    print("  快照 trade_date=%s  期望=%s  %s" % (snap_latest, date, "OK" if ok_snap else "!! 快照非当日，先跑 build_g2_daily"))
    # c) F6 估值当日性（2026-09-03 修复：估值必须反映当日 close，防再滞后 11 天）
    ok_f6 = _check_f6_live(snap_latest)
    print("-" * 60)
    all_ok = ok_incr and ok_snap and ok_f6
    print("  数据当日性: %s" % ("PASS" if all_ok else "FAIL（需处理后再 rebalance --live）"))
    return all_ok


def main():
    ap = argparse.ArgumentParser(description="G2 盘前核对")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    date = args.date or time.strftime("%Y%m%d")
    print("G2 盘前核对 date=%s  account=%s" % (date, G.ACCOUNT_ID))
    print("提示: rebalance --date 用【数据日期=前一交易日】。如 9/3 换仓应传 --date 20260902（候选 20260902_g2_top10.csv）。")
    r1 = check_heartbeat(date)
    r2 = check_candidate(date)
    check_overshoot(date)
    r4 = check_data_freshness(date)
    print("=" * 60)
    print("结论: %s" % ("ALL-CHECKED" if (r1 and r2 and r4) else "存在未就绪项，处理后再 rebalance --live"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
