# coding=utf-8
"""执行壳离线回归：直接 import 构建产物（GBK），只测纯逻辑，不需要 QMT。

覆盖：时段闸边界、卖腿按可卖量夹取、无信号/空订单/非交易时段/账号日期 schema 不符、
已执行去重、重复腿与非法股数整单放弃、买腿总额上限、position/order 字段兜底、
以及「vendor 是否真的注入了」。
"""
import importlib.util
import io
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
# 产物可能被 builder --live 覆盖成 False；「默认必须 True」这条应该钉在**源码**上
_src = io.open(os.path.join(HERE, "strategy_p16_g2_exec.py"), encoding="utf-8").read()
check("P0 源码 DRY_RUN 默认为 True（--live 才覆盖）", "DRY_RUN = True" in _src)
check("P0 产物 dry_run 取值只可能是 True/False", mod.DRY_RUN in (True, False))
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

# --- T10 成交归属：同账号多策略绝不能串账（2026-10-30 修）---
# G2 与 V1.3 共用 67014907，deal 接口返回整个账号的成交；只有 remark 能区分归属。
class D(object):
    def __init__(self, code, vol, px, remark, opt="证券买入"):
        self.m_strInstrumentID = code.split(".")[0]
        self.m_strExchangeID = code.split(".")[1]
        self.m_nVolume = vol
        self.m_dPrice = px
        self.m_strRemark = remark
        self.m_strOptName = opt


class _DealC(object):
    def __init__(self, deals):
        self.deals = deals

    def get_current_time(self):
        return "20261029100845"

    def get_trade_detail_data(self, acct, kind1, kind2):
        return self.deals if kind2 == "deal" else []


def _reset_fills():
    mod._g["fills_seen"] = set()
    mod._g["fill_no_remark_warned"] = False
    mod._g["legs_side"] = {}


reported = []
mod._report_fills_orig = getattr(mod, "_report_fills", None)
logs = []
mod._log_orig = getattr(mod, "_log", None)


def _spy(fills):
    reported.append(list(fills))


def _log_spy(msg):
    logs.append(str(msg))


mod._report_fills = _spy
mod._log = _log_spy

# 10a 本策略的成交必须上报，且方向取成交对象自带的操作名
_reset_fills()
mod._log_new_fills(_DealC([D("600000.SH", 300, 9.78, "g2-20261029-abcdef12-1")]), "g2-20261029-abcdef12")
check("T10a 本策略成交上报 1 笔", len(reported) == 1 and len(reported[0]) == 1)
check("T10a 方向取自 m_strOptName", reported[0][0]["side"] == "BUY")

# 10b 别的策略（V1.3）的成交绝不能被 G2 上报 —— 串账核心用例
reported[:] = []
_reset_fills()
mod._log_new_fills(
    _DealC([D("000906.SZ", 5200, 5.2, "v13-20261030-4ba709aa-1")]), "g2-20261030-23af1b58")
check("T10b 他人成交不上报（串账防线）", reported == [])
check("T10b 他人成交只打 FILL? 留痕", any("FILL?" in x for x in logs))

# 10c 同一批里既有本策略又有他人的，只上报本策略那条
reported[:] = []
_reset_fills()
mod._log_new_fills(_DealC([
    D("600000.SH", 300, 9.78, "g2-20261030-23af1b58-1"),
    D("000906.SZ", 5200, 5.2, "v13-20261030-4ba709aa-1"),
]), "g2-20261030-23af1b58")
check("T10c 混合批次只上报本策略 1 笔", len(reported) == 1 and len(reported[0]) == 1)
check("T10c 上报的是本策略标的", reported[0][0]["code"] == "600000.SH")

# 10d 全无 remark ⇒ 无法归属，一律不上报（宁可漏记不串账）
reported[:] = []
_reset_fills()
mod._log_new_fills(_DealC([D("600000.SH", 300, 9.78, "")]), "g2-20261030-23af1b58")
check("T10d 无 remark 不上报", reported == [])

# 10e 本轮无 sid（如重启后 last_sid 为空）⇒ 只留痕不上报
reported[:] = []
_reset_fills()
mod._log_new_fills(_DealC([D("600000.SH", 300, 9.78, "g2-20261030-23af1b58-1")]), None)
check("T10e 无 sid 不上报", reported == [])

# 10f 同一笔重复扫到只上报一次
reported[:] = []
_reset_fills()
d = D("600000.SH", 300, 9.78, "g2-20261030-23af1b58-1")
mod._log_new_fills(_DealC([d]), "g2-20261030-23af1b58")
mod._log_new_fills(_DealC([d]), "g2-20261030-23af1b58")
check("T10f 重复扫描只上报一次", len(reported) == 1)

if mod._report_fills_orig:
    mod._report_fills = mod._report_fills_orig
if mod._log_orig:
    mod._log = mod._log_orig

print("")
if fails:
    print("FAILED %d: %s" % (len(fails), " | ".join(fails)))
    sys.exit(1)
print("ALL PASS")
sys.exit(0)
