# coding=utf-8
"""执行壳离线回归：直接 import 构建产物（GBK），只测纯逻辑，不需要 QMT。

覆盖：时段闸边界、卖腿按可卖量夹取、无信号/空订单/非交易时段/账号日期 schema 不符、
已执行去重、重复腿与非法股数整单放弃、买腿总额上限、position/order 字段兜底、
以及「vendor 是否真的注入了」。
"""
import importlib.util
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
PROD = os.path.join(HERE, "build", "strategy_p16_g2_exec.py")

spec = importlib.util.spec_from_file_location("p16_exec_prod", PROD)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

fails = []


def check(name, cond):
    if cond:
        print("  OK   " + name)
    else:
        print("  FAIL " + name)
        fails.append(name)


def env(orders=None, **kw):
    e = {
        "schema": "p16.signal.v1",
        "signal_id": "g2-20261029-abcdef12",
        "trade_date": "20261029",
        "account_id": "67014907",
        "orders": orders if orders is not None else [
            {"action": "SELL", "code": "600000.SH", "vol": 300, "price": 9.78},
            {"action": "BUY", "code": "000001.SZ", "vol": 200, "price": 11.49},
        ],
    }
    e.update(kw)
    return e


TODAY = "20261029"
POS = {"600000.SH": {"vol": 500, "can_use": 300}, "000001.SZ": {"vol": 0, "can_use": 0}}

# --- 0) 产物基本形状 ---
check("P0 产物可 import（GBK + coding 声明）", mod is not None)
check("P0 vendor 已注入 fetch", callable(getattr(mod, "fetch", None)))
check("P0 vendor 已注入 already_applied", callable(getattr(mod, "already_applied", None)))
check("P0 DRY_RUN 默认 True（首次部署必须只读）", mod.DRY_RUN is True)
check("P0 BUILD_TAG 已替换成时间戳", mod.BUILD_TAG != "00000000-000000")
check("P0 ACCOUNT_ID 与资金池一致", mod.ACCOUNT_ID == "67014907")

# --- 1) 时段闸边界 ---
check("T1 09:29 不在时段", not mod.in_session(929))
check("T1 09:30 在时段", mod.in_session(930))
check("T1 11:30 在时段", mod.in_session(1130))
check("T1 11:31 不在时段（午休）", not mod.in_session(1131))
check("T1 13:00 在时段", mod.in_session(1300))
check("T1 14:57 在时段", mod.in_session(1457))
check("T1 14:58 不在时段", not mod.in_session(1458))
check("T1 15:00 收盘后不下单", not mod.in_session(1500))

# --- 1b) 执行窗口 09:50–09:58（与回测口径一致）---
check("T1b 09:49 不在窗口", not mod.in_exec_window(949))
check("T1b 09:50 在窗口", mod.in_exec_window(950))
check("T1b 09:52 在窗口", mod.in_exec_window(952))
check("T1b 09:58 在窗口（截止）", mod.in_exec_window(958))
check("T1b 09:59 已过窗口", not mod.in_exec_window(959))
check("T1b 14:00 在交易时段但不在窗口", mod.in_session(1400) and not mod.in_exec_window(1400))

# --- 2) 正常路径 ---
legs, info = mod.plan_legs(env(), TODAY, 952, POS, set(), True)
check("T2 正常信号出 2 条腿", len(legs) == 2 and info.startswith("ok"))
check("T2 remark 带 signal_id 与腿号",
      legs[0]["remark"] == "g2-20261029-abcdef12-1"
      and legs[1]["remark"] == "g2-20261029-abcdef12-2")
check("T2 卖腿按可卖量夹取（500→300）", legs[0]["vol"] == 300)
check("T2 买腿原样", legs[1]["action"] == "BUY" and legs[1]["vol"] == 200)

# --- 3) 夹取后为 0 的卖腿跳过（不报致命）---
legs, info = mod.plan_legs(env([{"action": "SELL", "code": "600000.SH", "vol": 300, "price": 9.78}]),
                          TODAY, 952, {"600000.SH": {"vol": 500, "can_use": 0}}, set(), True)
check("T3 无可卖量的卖腿被跳过且不致命", legs == [] and "无可执行腿" in info)

# --- 4) 拒绝路径 ---
cases = [
    ("空信号", None, 952, set(), "无信号"),
    ("schema 不符", env(schema="v0"), 952, set(), "schema"),
    ("日期不符", env(trade_date="20261028"), 952, set(), "日期不符"),
    ("账号不符", env(account_id="70180771"), 952, set(), "账号不符"),
    ("非执行窗口 12:00", env(), 1200, set(), "非执行窗口"),
    ("非执行窗口 10:30（同口径外时刻）", env(), 1030, set(), "非执行窗口"),
    ("已执行", env(), 1000, {"g2-20261029-abcdef12"}, "已执行"),
    ("空订单", env(orders=[]), 952, set(), "订单为空"),
]
for name, e, hm, applied, token in cases:
    legs, info = mod.plan_legs(e, TODAY, hm, POS, applied, True)
    check("T4 %s 被拒" % name, legs == [] and token in info)

# --- 5) 非法腿整单放弃 ---
bad_orders = [
    {"action": "BUY", "code": "000001.SZ", "vol": 150, "price": 11.49},   # 非整百
    {"action": "HOLD", "code": "600000.SH", "vol": 100, "price": 9.78},   # 动作非法
]
legs, info = mod.plan_legs(env(bad_orders), TODAY, 952, POS, set(), True)
check("T5 非法股数/动作 -> 整单放弃", legs == [] and "非法" in info)

dup = [
    {"action": "SELL", "code": "600000.SH", "vol": 100, "price": 9.78},
    {"action": "SELL", "code": "600000.SH", "vol": 100, "price": 9.78},
]
legs, info = mod.plan_legs(env(dup), TODAY, 952, POS, set(), True)
check("T5 重复腿 -> 整单放弃", legs == [] and "重复" in info)

# --- 6) 买腿总额上限 ---
big = [{"action": "BUY", "code": "000001.SZ", "vol": 20000, "price": 11.49}]
legs, info = mod.plan_legs(env(big), TODAY, 952, POS, set(), True)
check("T6 买腿超资金池 -> 拒", legs == [] and "超限" in info)

# --- 7) 字段兜底 ---
class P(object):
    def __init__(self, ins, ex, vol, can):
        self.m_strInstrumentID = ins
        self.m_strExchangeID = ex
        self.m_nVolume = vol
        self.m_nCanUseVolume = can


class O(object):
    def __init__(self, remark):
        self.m_strRemark = remark


pos = mod._extract_positions([P("600000", "SH", 500, 300), P("000001", "SZ", 0, 0)])
check("T7 position 对象解析", pos["600000.SH"]["can_use"] == 300 and pos["000001.SZ"]["vol"] == 0)
remarks = mod._extract_remarks([O("g2-20261029-abcdef12-1"), O("")])
check("T7 order remark 解析", remarks == ["g2-20261029-abcdef12-1"])

# --- 8) 去重函数（vendor）---
check("T8 remark 命中即视为已执行",
      mod.already_applied("g2-20261029-abcdef12", ["g2-20261029-abcdef12-2"]))
check("T8 无命中则未执行", not mod.already_applied("g2-xxx", ["other"]))

# --- T9 QMT 时间解析必须抗脏值（2026-10-29 实测 'product_...' 抛 ValueError）---
class _FakeC(object):
    def __init__(self, v):
        self.v = v

    def get_current_time(self):
        return self.v


import datetime as _dt
d9, hm9, _ = mod._qmt_now(_FakeC("20261029100845"))
check("T9 纯数字串 20261029100845 解析正确", d9 == "20261029" and hm9 == 1008)
d9, hm9, _ = mod._qmt_now(_FakeC(_dt.datetime(2026, 10, 29, 9, 52)))
check("T9 datetime 解析正确", d9 == "20261029" and hm9 == 952)
import time as _time
d9, hm9, _ = mod._qmt_now(_FakeC(_time.struct_time((2026, 10, 29, 9, 52, 0, 3, 302, 0))))
check("T9 struct_time（QMT 真实返回类型）解析正确", d9 == "20261029" and hm9 == 952)
d9, hm9, _ = mod._qmt_now(_FakeC("product_abc_xyz"))
check("T9 脏值不抛异常且退回本机时间", len(d9) == 8 and d9.isdigit() and hm9 >= 0)
d9, hm9, _ = mod._qmt_now(_FakeC(None))
check("T9 None 不抛异常", len(d9) == 8 and d9.isdigit())

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
