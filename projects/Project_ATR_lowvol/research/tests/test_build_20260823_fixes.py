# -*- coding: utf-8 -*-
"""T-20260823-001 build 验证：静态闸 + 沙箱功能单测。"""
import ast
import datetime
import io
import sys
import time

sys.path.insert(0, r"D:\Python311\Lib\site-packages")
import pandas as pd

SRC = r"D:\QuantLab\projects\Project_ATR_lowvol\build\strategy_atr_lowvol_equalweight.py"
OUT = r"D:\Temp\opencode\validate_20260823.txt"

# 用法：D:\Python311\python.exe research\tests\test_build_20260823_fixes.py
# T-20260823-001 验证闸：静态检查(GBK/py3.6/标记) + 沙箱功能单测(停牌判定/ATR口径/主循环状态机)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))


# ---------- 静态闸 ----------
raw = open(SRC, "rb").read()
check("S1 文件非空", len(raw) > 10000, "%d bytes" % len(raw))
try:
    text = raw.decode("gbk")
    check("S2 GBK 可解码", True)
except Exception as e:
    text = ""
    check("S2 GBK 可解码", False, repr(e))
first = text.split("\n")[0].strip()
check("S3 首行 coding=gbk", first.startswith("# coding=gbk"), first)
check("S4 BUILD_TAG=20260823-*", 'BUILD_TAG = "20260823-' in text)
_fstr = [n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.JoinedStr)]
check("S5 无 f-string(AST)", len(_fstr) == 0, str(len(_fstr)))
check("S6 无 walrus", ":=" not in text)
check("S7 无 MOCK", "MOCK" not in text.upper().replace("MOCK测试", ""))
check("S8 新账号默认", "_ACCOUNT_ID = '70180771'" in text)
check("S9 旧账号清除", "'67014907'" not in text)
for mark in [
    "def _is_suspended_bar(df):",
    "if _is_suspended_bar(df):",
    "in_trade_window = (933 <= hhmm_int <= 1455)",
    "(is_rebalance_day or force_retry) and in_trade_window",
    "if len(_g_my_codes) > 0:",
    "_g_last_attempt_ts = time.time()",
    "_RETRY_MIN_INTERVAL = 1800",
    "df = df[df['volume'] > 0]",
]:
    check("S10 标记[%s]" % mark[:34], mark in text)

tree = ast.parse(text)
check("S11 py3.6 语法", True)
try:
    ast.parse(text, feature_version=(3, 6))
    check("S12 ast feature_version=3.6", True)
except Exception as e:
    check("S12 ast feature_version=3.6", False, repr(e))

# ---------- 沙箱 exec ----------
ns = {"__name__": "atr_build_test"}
exec(compile(text, SRC, "exec"), ns)
check("E1 模块可整体exec", True)
check("E2 账号运行时值", ns["_ACCOUNT_ID"] == "70180771", str(ns["_ACCOUNT_ID"]))
check("E3 TAG运行时", ns["BUILD_TAG"].startswith("20260823-"), ns["BUILD_TAG"])

is_susp = ns["_is_suspended_bar"]


def mkdf(vols):
    return pd.DataFrame({"volume": vols, "close": [10.0] * len(vols)})


check("F1 末根单空bar放行", is_susp(mkdf([100.0, 50.0, 0.0])) is False)
check("F2 连续两根无量=停牌", is_susp(mkdf([100.0, 0.0, 0.0])) is True)
check("F3 全有量=不停牌", is_susp(mkdf([100.0, 50.0])) is False)
check("F4 NaN末根放行", is_susp(pd.DataFrame({"volume": [100.0, float("nan")]})) is False)
check("F5 空df=不停牌", is_susp(pd.DataFrame({"volume": []})) is False)
check("F6 None=不停牌", is_susp(None) is False)
check("F7 缺列=不停牌", is_susp(pd.DataFrame({"close": [1.0]})) is False)

calc = ns["_calc_atr_pct"]
rng = pd.DataFrame({
    "high": [10.0 + (i % 3) for i in range(30)],
    "low": [9.0 + (i % 2) for i in range(30)],
    "close": [9.5 + (i % 4) * 0.1 for i in range(30)],
    "volume": [1000.0] * 30,
})
v_clean = calc(rng)
dirty = pd.concat([rng, pd.DataFrame([
    {"high": c, "low": c, "close": c, "volume": 0.0}
    for c in [9.8] * 10
])], ignore_index=True)
v_dirty_input = calc(dirty)
check("F8 ATR剔平填后与干净口径一致", abs(v_clean - v_dirty_input) < 1e-12,
      "%.6f vs %.6f" % (v_clean, v_dirty_input))
flat_only_tail = rng.copy()
flat_only_tail.loc[flat_only_tail.index[-20:], "volume"] = 0.0
check("F9 平填过多时返回999", calc(flat_only_tail) == 999.0,
      str(calc(flat_only_tail)))

# ---------- _main_loop 流程沙箱 ----------
class FakeC(object):
    do_back_test = False
    do_backtest = False

    def __init__(self, now):
        self._now = now

    def is_last_bar(self):
        return True


def make_ns_now(dt):
    ns["_get_qmt_time"] = lambda C: dt


calls = {}


def reset_stubs(fill_codes):
    calls.clear()
    ns["_run_screening"] = lambda C: ["600000.SH", "600036.SH"]

    def _rebal(C, s):
        calls.setdefault("rebal", s)
        if fill_codes:
            ns["_g_my_codes"]["600000.SH"] = {"buy_price": 10.0, "shares": 100}

    ns["_rebalance_to_target"] = _rebal
    ns["_check_pending_orders"] = lambda C: None
    ns["_evaluate_interim_stops"] = lambda C, p: []
    ns["_current_prices"] = lambda C, codes: {}
    ns["_execute_sells"] = lambda C, t, p: None
    ns["_save_holdings"] = lambda: None


logs = []
ns["print"] = lambda *a, **k: logs.append(" ".join(str(x) for x in a))

# S-A 盘前09:16不触发
fri_0916 = datetime.datetime(2026, 8, 21, 9, 16, 57)
fri_0940 = datetime.datetime(2026, 8, 21, 9, 40, 0)
ns["_g_my_codes"] = {}
ns["_g_last_rebalance_key"] = ""
ns["_g_last_attempt_ts"] = 0.0
ns["_g_cooling_until"] = 0.0
reset_stubs(False)
make_ns_now(fri_0916)
ns["_main_loop"](FakeC(fri_0916))
check("M1 盘前不触发选股", "rebal" not in calls and not any("再平衡触发" in x for x in logs))

# S-B 盘中触发但建仓失败 -> 不锁键、记录尝试时刻
logs[:] = []
ns["_g_my_codes"] = {}
ns["_g_last_rebalance_key"] = ""
ns["_g_last_attempt_ts"] = 0.0
reset_stubs(False)
make_ns_now(fri_0940)
ns["_main_loop"](FakeC(fri_0940))
check("M2 触发了再平衡", any("再平衡触发" in x for x in logs))
check("M3 建仓失败不锁季度键", ns["_g_last_rebalance_key"] == "")
check("M4 尝试时刻已记录", ns["_g_last_attempt_ts"] > 0)
check("M5 有重试提示日志", any("建仓未成功" in x for x in logs))

# S-C 建仓成功 -> 锁键
logs[:] = []
ns["_g_my_codes"] = {}
ns["_g_last_rebalance_key"] = ""
ns["_g_last_attempt_ts"] = 0.0
reset_stubs(True)
ns["_main_loop"](FakeC(fri_0940))
check("M6 成功锁季度键", ns["_g_last_rebalance_key"] == "2026Q3")

# S-D 已锁键+有持仓 -> 不再触发，走止损评估
logs[:] = []
reset_stubs(True)
ns["_main_loop"](FakeC(fri_0940))
check("M7 键后不再触发", not any("再平衡触发" in x for x in logs))
check("M8 走止损评估", any("间际止损评估" in x for x in logs))

# S-E 重试间隔限频：失败后30分钟内不重试
logs[:] = []
ns["_g_my_codes"] = {}
ns["_g_last_rebalance_key"] = ""
ns["_g_last_attempt_ts"] = time.time() - 60
reset_stubs(False)
make_ns_now(datetime.datetime(2026, 8, 21, 10, 20, 0))
ns["_main_loop"](FakeC(datetime.datetime(2026, 8, 21, 10, 20, 0)))
check("M9 间隔内不重试", not any("再平衡触发" in x for x in logs))
logs[:] = []
real_time = time.time
time.time = lambda: real_time() + 1900
ns["_main_loop"](FakeC(datetime.datetime(2026, 8, 21, 10, 55, 0)))
time.time = real_time
check("M10 间隔过后重试", any("再平衡触发" in x for x in logs))

# S-F 周六不交易
sat = datetime.datetime(2026, 8, 22, 10, 0, 0)
logs[:] = []
ns["_g_my_codes"] = {}
ns["_g_last_rebalance_key"] = ""
ns["_g_last_attempt_ts"] = 0.0
reset_stubs(True)
make_ns_now(sat)
ns["_main_loop"](FakeC(sat))
check("M11 周末不触发", not any("再平衡触发" in x for x in logs))

# ---------- 输出 ----------
fails = [r for r in results if not r[1]]
with io.open(OUT, "w", encoding="utf-8") as f:
    for name, ok, detail in results:
        f.write(u"%s %s %s\n" % ("PASS" if ok else "FAIL", name, detail))
    f.write(u"\nTOTAL=%d PASS=%d FAIL=%d\n" % (len(results), len(results) - len(fails), len(fails)))
print("TOTAL=%d PASS=%d FAIL=%d" % (len(results), len(results) - len(fails), len(fails)))
