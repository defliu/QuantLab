# -*- coding: utf-8 -*-
"""T-20261009-007 reason 字段工程测试（track_sold_forward reason 列 + KNOWN_FAKE 剔除）。

背景（止损审计 §6.3 / §七.2，2026-10-09）：601999（+15.1% 一日止盈）曾被误判为止损，
根因是 sold_forward_live.csv 无 reason 列——账本 qmt_trade_log 的 score 列虽承载
reason（TRAE 4c-4「score/原因」约定），但前向台账看不到。另：300413 的 10-02
+108% 假成交（T-20261008-012）源头已从桥 fills 清除，但累计台账派生行永久残留。

方案一（诚哥拍板 2026-10-10）：不动 qmt_trade_log 8 列结构，reason 在
sold_forward 侧 join（账本 score 非数值=reason；桥 fills 自带 reason 戳）。

用例：
  R1 _reason_from_score：非数值=reason / 数值（0.0、80.0）=空（2026-08 旧约定前
     63 行如实为空，禁止猜测归因）/ 空串=空
  R2 KNOWN_FAKE_SELLS 绊线：300413 假成交条目必须在册（防清单被误清）
  R3 load_sells 账本源：SELL_TAKE_PROFIT/MATURE 提取为 reason、score=0.0 为空、
     BUY 行排除
  R4 load_sells 桥源：fill.reason 透传（SELL_STOP）、BUY fill 排除、坏 JSON 跳过
  R5 drop_known_fake：剔除假成交行且仅剔除该行（同票真行保留、vol 精确匹配）
  R6 build_tracker：reason 随行透传到台账行

运行（miniqmt venv）：
  C:/Users/Administrator/.workbuddy/binaries/python/envs/miniqmt/Scripts/python.exe research/tests/test_track_sold_forward_reason.py
"""
import json
import os
import sys
import tempfile

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

import track_sold_forward as T  # noqa: E402

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


def _fixture_trade_log(path):
    """写最小 qmt_trade_log.csv（8 列，TRAE 4c-4 口径）。"""
    rows = [
        "time,code,side,vol,price,score,order_id,account_id",
        "2026-09-07 10:07:09,601999.SH,BUY,6600,6.9,69.5,1098914258,67014907",
        "2026-09-08 13:30:10,601999.SH,SELL,6600,7.94,SELL_TAKE_PROFIT,1098929645,67014907",
        "2026-09-30 10:09:31,603237.SH,SELL,2800,15.2418,MATURE,1090531503,67014907",
        "2026-08-25 09:49:43,600016.SH,SELL,300,0.0,0.0,1082146267,67014907",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(rows) + "\n")


def _fixture_bridge(base):
    """写最小桥 state/fills 目录：一笔 SELL_STOP、一笔 BUY（应排除）、一个坏 JSON（应跳过）。"""
    sdir = os.path.join(base, "state")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, "fills_20261009.json"), "w", encoding="utf-8") as f:
        json.dump({
            "account_id": "70180771", "date": "20261009",
            "fills": [
                {"code": "688651.SH", "action": "SELL", "vol": 700, "price": 23.16,
                 "reason": "SELL_STOP", "ts": "2026-10-09 09:35:45"},
                {"code": "688117.SH", "action": "BUY", "vol": 1000, "price": 17.62,
                 "reason": "deal confirmed", "ts": "2026-10-09 09:59:03"},
            ],
        }, f, ensure_ascii=False)
    with open(os.path.join(sdir, "fills_broken.json"), "w", encoding="utf-8") as f:
        f.write("{not-json")


def test_reason_from_score():
    print("[R1] _reason_from_score")
    _check("非数值 reason 提取", T._reason_from_score("SELL_STOP") == "SELL_STOP")
    _check("MATURE 提取", T._reason_from_score("MATURE") == "MATURE")
    _check("数值 0.0 为空", T._reason_from_score("0.0") == "")
    _check("数值 80.0 为空", T._reason_from_score("80.0") == "")
    _check("空串为空", T._reason_from_score("") == "")
    _check("None 为空", T._reason_from_score(None) == "")
    _check("带空白非数值提取", T._reason_from_score(" SELL_TAKE_PROFIT ") == "SELL_TAKE_PROFIT")


def test_known_fake_tripwire():
    print("[R2] KNOWN_FAKE_SELLS 绊线")
    entry = ("300413.SZ", "2026-10-02", 1000, "70180771")
    _check("300413 假成交在册（含 acct 四元组）", entry in T.KNOWN_FAKE_SELLS,
           "当前清单: %r" % (T.KNOWN_FAKE_SELLS,))


def test_load_sells_trade_log(tmpdir):
    print("[R3] load_sells 账本源 reason 提取")
    log = os.path.join(tmpdir, "qmt_trade_log.csv")
    _fixture_trade_log(log)
    old_log, old_bridges = T.TRADE_LOG, T.BRIDGES
    try:
        T.TRADE_LOG, T.BRIDGES = log, []
        rows = T.load_sells()
        by_code = {r["code"]: r for r in rows}
        _check("SELL 行数=3（BUY 排除）", len(rows) == 3, "实际 %d" % len(rows))
        _check("601999 → SELL_TAKE_PROFIT",
               by_code.get("601999.SH", {}).get("reason") == "SELL_TAKE_PROFIT",
               repr(by_code.get("601999.SH")))
        _check("603237 → MATURE",
               by_code.get("603237.SH", {}).get("reason") == "MATURE")
        _check("600016 score=0.0 → reason 空（禁止猜测）",
               by_code.get("600016.SH", {}).get("reason") == "")
        _check("acct 透传", by_code.get("601999.SH", {}).get("acct") == "67014907")
    finally:
        T.TRADE_LOG, T.BRIDGES = old_log, old_bridges


def test_load_sells_bridge(tmpdir):
    print("[R4] load_sells 桥 fills reason 透传")
    _fixture_bridge(tmpdir)
    old_log, old_bridges = T.TRADE_LOG, T.BRIDGES
    try:
        T.TRADE_LOG, T.BRIDGES = os.path.join(tmpdir, "no_such.csv"), [tmpdir]
        rows = T.load_sells()
        _check("仅 1 笔桥 SELL（BUY 排除+坏 JSON 跳过）", len(rows) == 1,
               "实际 %d" % len(rows))
        if rows:
            r = rows[0]
            _check("688651 → SELL_STOP", r["reason"] == "SELL_STOP", repr(r))
            _check("acct=70180771", r["acct"] == "70180771")
            _check("src=bridge_fills", r["src"] == "bridge_fills")
    finally:
        T.TRADE_LOG, T.BRIDGES = old_log, old_bridges


def test_drop_known_fake():
    print("[R5] drop_known_fake 精确剔除")
    df = pd.DataFrame([
        {"time": "2026-10-02 09:30:00", "code": "300413.SZ", "vol": "1000",
         "acct": "70180771", "reason": "SELL_STOP"},          # 假成交（应剔除）
        {"time": "2026-09-09 14:34:39", "code": "300413.SZ", "vol": "2000",
         "acct": "70180771", "reason": "SELL_STOP"},          # 同票真行（保留）
        {"time": "2026-10-02 10:00:00", "code": "300413.SZ", "vol": "1000",
         "acct": "67014907", "reason": ""},                   # 同日同量异账号（保留）
        {"time": "2026-10-09 09:35:45", "code": "688651.SH", "vol": "700",
         "acct": "70180771", "reason": "SELL_STOP"},          # 无关行（保留）
    ])
    out, n = T.drop_known_fake(df)
    _check("剔除恰 1 行", n == 1, "实际 %d" % n)
    _check("假成交行已消失",
           not ((out["code"] == "300413.SZ") & (out["time"].str.startswith("2026-10-02 09:30"))).any())
    _check("同票真行保留", len(out[out["time"].str.startswith("2026-09-09 14:34")]) == 1)
    _check("总行数 4→3", len(out) == 3, "实际 %d" % len(out))
    empty_out, n0 = T.drop_known_fake(pd.DataFrame(columns=["time", "code", "vol"]))
    _check("空表安全", n0 == 0 and len(empty_out) == 0)


def test_build_tracker_reason():
    print("[R6] build_tracker reason 透传")
    daily = {"601999.SH": pd.DataFrame({
        "trade_date": ["2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11", "2026-09-12",
                        "2026-09-15", "2026-09-16"],
        "open": [7.94] * 7,
        "close": [7.9, 7.8, 7.7, 7.6, 7.5, 7.4, 7.3],
    })}
    sells = [{
        "time": "2026-09-08 13:30:10", "code": "601999.SH", "vol": 6600,
        "price": 7.94, "acct": "67014907", "src": "qmt_trade_log",
        "reason": "SELL_TAKE_PROFIT",
    }]
    df = T.build_tracker(sells, daily)
    _check("回填 1 行", len(df) == 1)
    if len(df):
        _check("reason 透传", df.iloc[0]["reason"] == "SELL_TAKE_PROFIT",
               repr(df.iloc[0].get("reason")))
        _check("exit 价保留", abs(df.iloc[0]["exit"] - 7.94) < 1e-9)


def main():
    tmp = tempfile.mkdtemp(prefix="tsf_reason_")
    test_reason_from_score()
    test_known_fake_tripwire()
    test_load_sells_trade_log(tmp)
    test_load_sells_bridge(os.path.join(tmp, "bridge"))
    test_drop_known_fake()
    test_build_tracker_reason()
    print("")
    print("PASS=%d FAIL=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
