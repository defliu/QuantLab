# coding: utf-8
"""P13 黄氏529 QMT 策略 MOCK 测试套件（T4）。

覆盖任务书 T4 要求的 ≥14 用例：
 信号读取（正常/缺失/空）/ 槽位与 1/12 上限 / 现金不足分配 / 止损触发 /
 trail 触发（含高点更新）/ 到期 / T+1 / 涨停不买 / 跌停进暂缓 + 解封补卖 /
 状态恢复 / reconcile 三态 / 回滚撤单 / 重复 bar 幂等 / CSV 导出。

隔离：全部 IO 走临时沙箱目录（不触碰 D:/QMT_POOL 真实文件）；
      passorder / get_trade_detail_data 注入 MockC 实例方法，绝不实盘下单。

运行: D:\\Python311\\python.exe research\\tests\\test_strategy_huang529_mock.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
from datetime import datetime

BUILD_PATH = "D:/QuantLab/projects/Project_13_529主升浪/build/strategy_huang529.py"
ST = None
C = None
SANDBOX = tempfile.mkdtemp(prefix="p13_mock_")

PASS = 0
FAIL = 0
FAILED = []


def load_module():
    global ST
    spec = importlib.util.spec_from_file_location("s", BUILD_PATH)
    ST = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ST)
    # 隔离：沙箱目录覆盖全部文件 IO 路径
    ST.SIGNAL_DIR = SANDBOX
    ST.DATA_DIR = SANDBOX
    ST.HOLDINGS_FILE = os.path.join(SANDBOX, "p13_huang529_holdings.json")
    ST.NAV_FILE = os.path.join(SANDBOX, "p13_huang529_nav.json")
    ST.TRADE_LOG_FILE = os.path.join(SANDBOX, "p13_huang529_trade_log.csv")


class MockC(object):
    """本地 MOCK C 对象：get_market_data_ex / get_current_time / passorder。
    记录 passorder 调用到 C.calls，不实盘下单。"""

    def __init__(self, now=None):
        self.prices = {}          # code -> close
        self.limit_up = set()
        self.limit_down = set()
        self.suspended = set()
        self.calls = []           # passorder 调用记录
        self.cancels = []         # cancel() 调用记录（oid, acct, acct_type）
        self.not_cancelable = set()  # can_cancel_order 返回 False 的委托号
        self.order_data = {}      # code -> order 对象 dict（反查）
        self.deals = []           # 成交明细 list（_reconcile 用）
        self.positions = {}       # 账户 position: code -> volume
        self.now = now or datetime(2026, 8, 18, 9, 40, 0)
        # passorder 递增单号：真实 QMT 客户端侧该值常为 0/None（异步接口），
        # 但也可能是有效委托号。两种口径都要能测，故提供可控开关。
        self.order_seq = 90000
        self.passorder_ret_zero = False   # True 时模拟「返回 0/None」的异步口径
        self.register_orders = False      # True 时 passorder 自动把委托写进 order_data

    def set_price(self, code, price):
        self.prices[code] = price

    def get_market_data_ex(self, fields, codes, period="1d"):
        import pandas as pd
        out = {}
        if isinstance(codes, str):
            codes = [codes]
        for c in codes:
            p = self.prices.get(c)
            if p is None:
                out[c] = pd.DataFrame()
                continue
            if c in self.limit_up:
                out[c] = pd.DataFrame({"close": [p], "pre_close": [round(p / 1.1, 2)]})
                continue
            if c in self.limit_down:
                out[c] = pd.DataFrame({"close": [round(p * 0.9, 2)], "pre_close": [p]})
                continue
            d = {}
            for f in fields:
                if f == "close":
                    d[f] = [p]
                elif f == "open":
                    d[f] = [0.0] if c in self.suspended else [p]
                elif f == "volume":
                    d[f] = [0] if c in self.suspended else [1000]
                elif f == "pre_close":
                    d[f] = [p]
                else:
                    d[f] = [0.0]
            out[c] = pd.DataFrame(d)
        return out

    def get_current_time(self):
        return self.now

    def passorder(self, *args):
        """模拟下单。返回可预测递增单号；passorder_ret_zero=True 时返回 0（异步口径）。

        passorder 入参布局与策略一致：passorder(opType, 1101, acct, code, 5, -1,
        volume, remark, 2, "", C) -> args[3]=code, args[6]=volume, args[7]=remark。
        register_orders=True 时把新委托写进 order_data，供反查路径直接命中。
        """
        self.calls.append(args)
        if self.passorder_ret_zero:
            return 0
        self.order_seq += 1
        oid = str(self.order_seq)
        if self.register_orders:
            code = str(args[3]) if len(args) > 3 else ""
            try:
                vol = int(float(args[6]))
            except Exception:
                vol = 0
            opt = "买入" if str(args[0]) == "23" else "卖出"
            remark = str(args[7]) if len(args) > 7 else ""
            self.order_data[code] = make_order(code, opt=opt, oid=oid,
                                               sysid="SYS-%s" % oid,
                                               traded=0, remark=remark,
                                               volume=vol)
        return int(oid)

    def cancel(self, oid, acct, acct_type, ctx=None):
        self.cancels.append((oid, acct, acct_type))
        return 0

    def can_cancel_order(self, oid, acct, acct_type):
        return oid not in self.not_cancelable

    def get_trade_detail_data(self, acct, typ, kind):
        if kind == "order":
            return list(self.order_data.values())
        if kind == "deal":
            return list(self.deals)
        if kind == "position":
            out = []
            for code, vol in self.positions.items():
                out.append(type("POS", (), {
                    "m_strInstrumentID": code,
                    "m_nVolume": vol,
                    "m_nCanUseVolume": vol,
                    "m_dOpenPrice": 0.0,
                })())
            return out
        return []


def reset(C, cash=100000.0):
    ST._cash = cash
    ST._holdings = {}
    ST._pending_orders = {}
    ST._today_orders = {}
    ST._suspended_sells = []
    ST._last_decision_date = ""
    ST._last_reconcile_date = ""
    ST._last_close_task_date = ""
    ST._last_pending_min = -1
    ST._last_hb_min = -1
    ST.CAPITAL_BASE = cash
    # 注入 QMT 全局函数（策略内以全局名调用）
    ST.passorder = C.passorder
    ST.get_trade_detail_data = C.get_trade_detail_data
    ST.cancel = C.cancel
    ST.can_cancel_order = C.can_cancel_order
    # 清空 MockC 全部状态，避免跨用例污染
    C.calls = []
    C.cancels = []
    C.not_cancelable = set()
    C.prices = {}
    C.limit_up = set()
    C.limit_down = set()
    C.suspended = set()
    C.order_data = {}
    C.deals = []
    C.positions = {}
    C.order_seq = 90000
    C.passorder_ret_zero = False
    C.register_orders = False


def write_signal(codes_ranks):
    """写信号 CSV 到沙箱（文件名 20260817 < 今日 20260818）。"""
    p = os.path.join(SANDBOX, "529_signal_top16_20260817.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write("ts_code,atr_pct,rank\n")
        for code, atr, rank in codes_ranks:
            f.write("%s,%.1f,%d\n" % (code, atr, rank))


def buy_code(code, price, volume, cost=None, entry_date="20260801", peak=None, hold_days=0):
    ST._holdings[code] = {
        "volume": volume,
        "cost": cost if cost is not None else price,
        "entry_date": entry_date,
        "peak_close": peak if peak is not None else price,
        "hold_days": hold_days,
    }
    C.set_price(code, price)


def make_order(code, opt="买入", oid="12345", sysid="SYS-777", traded=0,
               status=None, remark="P13H529", volume=1000):
    """构造 QMT 委托对象。status=None 表示不返回该字段（未知态，须保守当活跃）。"""
    d = {
        "m_strInstrumentID": code,
        "m_strOptName": opt,
        "m_nOrderID": oid,
        "m_nVolumeTraded": traded,
        "m_nOrderVolume": volume,
        "m_strRemark": remark,
        "m_strInsertTime": "09:35:00",
    }
    if sysid is not None:
        d["m_strOrderSysID"] = sysid
    if status is not None:
        d["m_nOrderStatus"] = status
    return type("ORD", (), d)()


def make_deal(code, opt="买入", vol=400.0, price=10.5, remark="P13H529",
              oid=None, sysid=None):
    """构造 QMT 成交对象（_reconcile 用 getattr 读属性，必须是对象不是 dict）。

    oid/sysid 用于 _deal_traded_for_order 的按单号聚合（deal 领先快照校准）。
    """
    d = {
        "m_strSecurityCode": code,
        "m_strOptName": opt,
        "m_strRemark": remark,
        "m_nVolume": vol,
        "m_nPrice": price,
    }
    if oid is not None:
        d["m_nOrderID"] = oid
    if sysid is not None:
        d["m_strOrderSysID"] = sysid
    return type("DEAL", (), d)()


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  [FAIL] %s" % name)


# ==================== pytest 兼容 bootstrap ====================
# 本文件是自研 runner（自带 PASS/FAIL 计数）：load_module()/C 原本只在 main() 里
# 初始化，而 main() 仅由 __main__ 块触发。pytest 以 test_*.py 收集本文件时不会执行
# __main__ -> ST/C 恒为 None -> 33 个用例全部误报 AttributeError: 'NoneType' has no
# attribute '_cash'。故改为导入即初始化，并对沙箱做 atexit 兜底清理（pytest 路径
# 不经过 main() 的 rmtree，否则每次收集都会遗留一个 p13_mock_* 临时目录）。
import atexit

if ST is None:
    load_module()
if C is None:
    C = MockC()
atexit.register(shutil.rmtree, SANDBOX, True)


# ==================== 用例 ====================
def test_signal_normal():
    print("[T4-01] 信号读取（正常）")
    reset(C)
    write_signal([("600000.SH", 2.0, 1), ("000001.SZ", 3.0, 2)])
    sigs = ST._load_signal_csv()
    check("读取 2 只并按 rank 升序", sigs == [("600000.SH", 2.0, 1), ("000001.SZ", 3.0, 2)])


def test_signal_missing():
    print("[T4-02] 信号缺失（fail-open）")
    reset(C)
    for p in os.listdir(SANDBOX):
        if p.startswith("529_signal_top16_") and p.endswith(".csv"):
            os.remove(os.path.join(SANDBOX, p))
    sigs = ST._load_signal_csv()
    check("缺失返回空列表（fail-open）", sigs == [])


def test_signal_empty():
    print("[T4-03] 信号空文件")
    reset(C)
    p = os.path.join(SANDBOX, "529_signal_top16_20260817.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write("ts_code,atr_pct,rank\n")
    sigs = ST._load_signal_csv()
    check("空文件返回空列表", sigs == [])


def test_slot_and_cap():
    print("[T4-04] 槽位与 1/12 单票上限")
    reset(C, 100000)
    write_signal([("600%03d.SH" % j, 2.0 + j, j + 1) for j in range(6)])
    for j in range(6):
        C.set_price("600%03d.SH" % j, 10.0)
    ST._decision(C, C.now)
    n_buy = sum(1 for c in C.calls if c[0] == 23)
    check("单日买入数量=信号数 6", n_buy == 6)
    for code, info in ST._holdings.items():
        check("单票市值<=总资产/12", info["volume"] * 10.0 <= 100000 / 12.0 + 1)


def test_cash_insufficient():
    print("[T4-05] 现金不足分配")
    reset(C, 1000)
    C.set_price("600000.SH", 20.0)
    write_signal([("600000.SH", 2.0, 1)])
    ST._decision(C, C.now)
    check("现金 1000 不足 100 股 -> 不买入", len(C.calls) == 0)


def test_stop_loss():
    print("[T4-06] 止损触发（-12% 收盘口径）")
    reset(C)
    buy_code("600000.SH", 10.0, 1000, cost=10.0, entry_date="20260801")
    C.set_price("600000.SH", 8.7)   # -13%
    write_signal([("600001.SH", 2.0, 1)])
    ST._decision(C, C.now)
    check("止损触发卖出", "600000.SH" not in ST._holdings)
    check("卖出已登记对账（dir=sell）",
          ST._today_orders.get("600000.SH", {}).get("dir") == "sell")


def test_trailing_peak():
    print("[T4-07] trail 触发（含高点更新）")
    reset(C)
    ST.TRAILING_STOP = 0.15
    buy_code("600000.SH", 10.0, 1000, cost=10.0, entry_date="20260801", peak=10.0)
    write_signal([("600001.SH", 2.0, 1)])
    C.set_price("600000.SH", 12.0)  # 新高
    ST._decision(C, C.now)
    check("peak 更新到 12.0", abs(ST._holdings["600000.SH"]["peak_close"] - 12.0) < 1e-6)
    C.set_price("600000.SH", 10.1)  # 回撤 15.8% > 15%
    ST._last_decision_date = ""
    ST._decision(C, C.now)
    check("trail 触发卖出", "600000.SH" not in ST._holdings)
    ST.TRAILING_STOP = None


def test_expiry():
    print("[T4-08] 到期卖出（60 交易日）")
    reset(C)
    buy_code("600000.SH", 10.0, 1000, cost=10.0, entry_date="20260401", hold_days=58)
    C.set_price("600000.SH", 11.0)
    write_signal([("600001.SH", 2.0, 1)])
    ST._decision(C, C.now)
    check("hold_days=58 -> 递增为 59 未触发", ST._holdings["600000.SH"]["hold_days"] == 59)
    check("未卖出", "600000.SH" in ST._holdings)
    ST._last_decision_date = ""
    C.set_price("600000.SH", 11.0)
    ST._decision(C, C.now)
    check("hold_days=60 -> 到期卖出", "600000.SH" not in ST._holdings)


def test_t_plus_1():
    print("[T4-09] T+1：当日买入不卖")
    reset(C)
    today = C.now.strftime("%Y%m%d")
    buy_code("600000.SH", 10.0, 1000, cost=10.0, entry_date=today)
    C.set_price("600000.SH", 8.5)   # 深跌但当日买入
    write_signal([("600001.SH", 2.0, 1)])
    ST._decision(C, C.now)
    check("当日买入即使大跌不卖", "600000.SH" in ST._holdings)


def test_limit_up_no_buy():
    print("[T4-10] 涨停不买")
    reset(C)
    C.set_price("600000.SH", 10.0)
    C.limit_up = {"600000.SH"}
    write_signal([("600000.SH", 2.0, 1)])
    ST._decision(C, C.now)
    check("涨停跳过买入", "600000.SH" not in ST._holdings)
    check("无买入委托", all(c[0] != 23 for c in C.calls))


def test_limit_down_pending():
    print("[T4-11] 跌停进暂缓队列 + 解封补卖")
    reset(C)
    buy_code("600000.SH", 10.0, 1000, cost=10.0, entry_date="20260801")
    C.set_price("600000.SH", 8.0)   # 跌停（close=8.0*0.9=7.2 -> -28% 触发止损）
    C.limit_down = {"600000.SH"}
    write_signal([("600001.SH", 2.0, 1)])
    ST._decision(C, C.now)
    check("跌停时保留持仓", "600000.SH" in ST._holdings)
    check("跌停进暂缓队列", "600000.SH" in ST._suspended_sells)
    C.limit_down = set()
    ST._last_decision_date = ""
    C.set_price("600000.SH", 8.0)
    ST._decision(C, C.now)
    check("解封后补卖", "600000.SH" not in ST._holdings)


def test_state_restore():
    print("[T4-12] 状态恢复（holdings json）")
    reset(C)
    buy_code("600000.SH", 10.0, 1000, cost=10.0)
    ST._save_holdings()
    ST._holdings = {}
    ST._load_holdings()
    check("状态恢复持仓 1 只", len(ST._holdings) == 1)
    check("volume 恢复", ST._holdings["600000.SH"]["volume"] == 1000)


def test_reconcile_three_state():
    print("[T4-13] reconcile 三态兜底")
    # 买入成交（账户有货 -> 以 position 兜底建仓）
    reset(C)
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0, "entry_date": "20260818"}
    C.positions["600000.SH"] = 1000
    ST._reconcile(C)
    check("买入成交兜底（账户有货）持仓 1000", ST._holdings.get("600000.SH", {}).get("volume", 0) == 1000)
    # 卖出未成交（账户仍有货 -> 恢复持仓）
    reset(C)
    ST._today_orders["600001.SH"] = {"dir": "sell", "amount": 500, "price": 11.0,
                                     "entry_price": 10.0, "entry_date": "20260801"}
    C.positions["600001.SH"] = 500
    ST._holdings = {"600001.SH": {"volume": 500, "cost": 10.0, "entry_date": "20260801",
                                  "peak_close": 10.0, "hold_days": 5}}
    ST._reconcile(C)
    check("卖出未成交恢复持仓", ST._holdings.get("600001.SH", {}).get("volume", 0) == 500)
    # 卖出已成交（账户无货 -> 持仓清零）
    reset(C)
    ST._today_orders["600002.SH"] = {"dir": "sell", "amount": 500, "price": 11.0,
                                     "entry_price": 10.0, "entry_date": "20260801"}
    ST._holdings = {"600002.SH": {"volume": 500, "cost": 10.0, "entry_date": "20260801",
                                  "peak_close": 10.0, "hold_days": 5}}
    ST._reconcile(C)
    check("卖出已成交持仓清零", "600002.SH" not in ST._holdings)


def test_rollback_cancel():
    print("[T4-14] 回滚先撤活单（官方 cancel，非 passorder 卖出语义）")
    reset(C)
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0, "entry_date": "20260818"}
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260818",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    C.order_data["600000.SH"] = make_order("600000.SH")
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 2}
    done = ST._rollback_pending(C, "600000.SH", info)
    # 撤单必须走官方 cancel()，且用柜台合同号 m_strOrderSysID
    check("回滚前调用 cancel() 撤单", len(C.cancels) == 1)
    check("cancel 使用柜台合同号 sysid", C.cancels and C.cancels[0][0] == "SYS-777")
    check("cancel 账号/类型正确", C.cancels and C.cancels[0][1] == "70180771" and C.cancels[0][2] == "stock")
    # 关键红线：撤单绝不能再发出 passorder 卖出语义的委托
    check("撤单未误用 passorder(24) 卖出语义", not any(a[0] == 24 for a in C.calls))
    # 红线：旧单仍在场内（状态未知=保守活跃）-> 不得回滚账本
    check("旧单仍活跃时不回滚账本（done=False）", done is False)
    check("旧单仍活跃时虚拟持仓保留", "600000.SH" in ST._holdings)
    check("旧单仍活跃时现金未回滚", abs(ST._cash - 90000.0) < 1e-6)
    check("已进入 rollback 确认态", ST._pending_orders["600000.SH"].get("cancel_action") == "rollback")


def test_rollback_after_cancel_confirmed():
    print("[T4-20] 撤单终态确认后才回滚账本")
    reset(C)
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0, "entry_date": "20260818"}
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260818",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    # 53=已撤，旧单已死透
    C.order_data["600000.SH"] = make_order("600000.SH", status=53)
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 2}
    done = ST._rollback_pending(C, "600000.SH", info)
    check("已撤终态直接回滚（done=True）", done is True)
    check("买入回滚撤销虚拟持仓", "600000.SH" not in ST._holdings)
    check("买入回滚返还现金 100000", abs(ST._cash - 100000.0) < 1e-6)
    check("已撤终态不再发撤单", len(C.cancels) == 0)


def test_rollback_orphan_order_quarantine():
    print("[T4-21] 查不到原委托 -> 隔离，不回滚不重下")
    reset(C)
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0, "entry_date": "20260818"}
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260818",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 2}
    done = ST._rollback_pending(C, "600000.SH", info)
    check("查不到单不擅自回滚", done is False)
    check("查不到单保留虚拟持仓待对账", "600000.SH" in ST._holdings)
    check("查不到单现金不动", abs(ST._cash - 90000.0) < 1e-6)
    check("查不到单标记隔离待对账", ST._pending_orders["600000.SH"].get("cancel_unconfirmed") is True)
    check("查不到单无撤单无新单", len(C.cancels) == 0 and len(C.calls) == 0)


def test_cancel_uses_order_id_when_no_sysid():
    print("[T4-17] 无 sysid 时退回 m_nOrderID 撤单")
    reset(C)
    ok = ST._cancel_order_by(C, "600000.SH", "12345", "")
    check("无 sysid 仍可撤单", ok is True)
    check("退回使用 m_nOrderID", C.cancels and C.cancels[0][0] == "12345")


def test_cancel_not_cancelable_blocks_retry():
    print("[T4-18] 不可撤时放弃重下（防叠单爆仓）")
    reset(C)
    C.order_data["600000.SH"] = make_order("600000.SH")
    C.not_cancelable.add("SYS-777")
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 1}
    ST._retry_pending(C, "600000.SH", info)
    check("不可撤时不发 cancel", len(C.cancels) == 0)
    check("不可撤时不重下新单（防叠单）", len(C.calls) == 0)
    check("不可撤时置隔离态等对账", ST._pending_orders["600000.SH"].get("cancel_unconfirmed") is True)


def test_retry_pending_two_phase():
    print("[T4-19] 撤单重下两阶段：cancel() 返回 != 已撤，终态确认后才重下")
    reset(C)
    C.order_data["600000.SH"] = make_order("600000.SH")   # 状态未知 -> 保守活跃
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 1}
    ST._pending_orders["600000.SH"] = info
    ST._retry_pending(C, "600000.SH", info)
    check("第一阶段：已发 cancel", len(C.cancels) == 1)
    check("第一阶段：绝不重下（cancel 返回≠已撤）", len(C.calls) == 0)
    check("第一阶段：进入 retry 确认态",
          ST._pending_orders["600000.SH"].get("cancel_action") == "retry"
          and ST._pending_orders["600000.SH"].get("cancel_requested") is True)
    # 第二阶段：反查仍活跃 -> 继续不重下
    ST._confirm_cancel(C, "600000.SH", ST._pending_orders["600000.SH"],
                       [C.order_data["600000.SH"]])
    check("第二阶段：旧单仍活跃时不重下", len(C.calls) == 0)
    check("第二阶段：仍保留在 pending 跟踪", "600000.SH" in ST._pending_orders)
    # 第三阶段：反查已撤终态 -> 才重下
    C.order_data["600000.SH"] = make_order("600000.SH", status=53)
    ST._confirm_cancel(C, "600000.SH", ST._pending_orders["600000.SH"],
                       [C.order_data["600000.SH"]])
    check("第三阶段：终态确认后才重下", len(C.calls) == 1)
    check("重下为买入 opType 23", C.calls and C.calls[0][0] == 23)
    check("重试次数已累加到 2", ST._pending_orders["600000.SH"]["retries"] == 2)
    check("重下后清除确认态标记",
          not ST._pending_orders["600000.SH"].get("cancel_requested"))


def test_confirm_cancel_filled_no_reorder():
    print("[T4-22] 撤单期间已全成(56)：不重下不回滚，交收盘对账")
    reset(C)
    C.order_data["600000.SH"] = make_order("600000.SH", status=56, traded=1000)
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 1}
    ST._pending_orders["600000.SH"] = info
    ST._confirm_cancel(C, "600000.SH", info, [C.order_data["600000.SH"]])
    check("已全成不重下（防超买）", len(C.calls) == 0)
    check("已全成移出跟踪", "600000.SH" not in ST._pending_orders)


def test_confirm_cancel_part_filled_reorder_remainder():
    print("[T4-23] 撤单期间部成：按剩余量重下，不按原量")
    reset(C)
    # 55=部成（活跃态）：先校准剩余
    C.order_data["600000.SH"] = make_order("600000.SH", status=55, traded=400)
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 1,
            "cancel_requested": True, "cancel_action": "retry",
            "cancel_poll": 0, "cancel_resend": ST.CANCEL_RESEND_MAX}
    ST._pending_orders["600000.SH"] = info
    ST._confirm_cancel(C, "600000.SH", info, [C.order_data["600000.SH"]])
    check("部成仍活跃 -> 不重下", len(C.calls) == 0)
    check("部成量已校准为剩余 600", info.get("amount") == 600)
    # 随后已撤终态 -> 按 600 重下
    C.order_data["600000.SH"] = make_order("600000.SH", status=54, traded=400)
    ST._confirm_cancel(C, "600000.SH", ST._pending_orders["600000.SH"],
                       [C.order_data["600000.SH"]])
    check("终态后按剩余量重下", len(C.calls) == 1)
    check("重下数量为 600 而非 1000", C.calls and C.calls[0][6] == 600)


def test_confirm_cancel_unconfirmed_quarantine():
    print("[T4-24] 反查不到原单达上限 -> 隔离到收盘对账（不重下不回滚）")
    reset(C)
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 1,
            "cancel_requested": True, "cancel_action": "retry",
            "cancel_poll": ST.CANCEL_CONFIRM_MAX_POLL - 1, "cancel_resend": 0}
    ST._pending_orders["600000.SH"] = info
    ST._confirm_cancel(C, "600000.SH", info, [])
    check("查不到单不重下", len(C.calls) == 0)
    check("查不到单不回滚账本", "rolled_back" not in ST._today_orders.get("600000.SH", {}))
    check("达上限后标记隔离", ST._pending_orders["600000.SH"].get("cancel_unconfirmed") is True)
    check("隔离态仍保留跟踪待对账", "600000.SH" in ST._pending_orders)


def test_order_status_terminal_matrix():
    print("[T4-25] 委托状态码终态矩阵（55 必须算活跃）")
    reset(C)
    check("56 全成=终态", ST._order_is_active(make_order("600000.SH", status=56)) is False)
    check("53 已撤=终态", ST._order_is_active(make_order("600000.SH", status=53)) is False)
    check("54 部撤=终态", ST._order_is_active(make_order("600000.SH", status=54)) is False)
    check("57 废单=终态", ST._order_is_active(make_order("600000.SH", status=57)) is False)
    check("55 部成=活跃（红线）", ST._order_is_active(make_order("600000.SH", status=55)) is True)
    check("48/49/50=活跃", all(
        ST._order_is_active(make_order("600000.SH", status=s)) is True
        for s in (2, 48, 49, 50)))
    check("状态缺失=保守活跃", ST._order_is_active(make_order("600000.SH", status=None)) is True)


def _mk_pending(direction="buy", amount=1000, price=10.0, retries=1,
                entry_price=9.0, tstr="2026-08-18 09:35:00",
                attempt_order_id="", attempt_sysid="", attempt_amount=None,
                attempt_traded=0, filled_before=0, cancel_fields=None):
    """构造符合新 attempt 口径的 pending（attempt_amount 与 amount 同源）。

    attempt_order_id/attempt_sysid 用于精确委托号匹配测试；
    cancel_fields 用于三态撤单字段（cancel_requested/sent/unconfirmed 等）注入。
    """
    y, mo, d, hh, mm, _ss = [int(x) for x in
                              tstr.replace("-", " ").replace(":", " ").split()]
    info = {
        "type": direction,
        "amount": amount,
        "original_amount": amount,
        "attempt_amount": amount if attempt_amount is None else attempt_amount,
        "attempt_traded": attempt_traded,
        "filled_before": filled_before,
        "filled_amount": filled_before + attempt_traded,
        "attempt_order_id": attempt_order_id,
        "attempt_sysid": attempt_sysid,
        "price": price,
        "time": datetime(y, mo, d, hh, mm, 0),
        "retries": retries,
        "entry_price": entry_price,
        "entry_date": "20260801",
    }
    if cancel_fields:
        info.update(cancel_fields)
    return info


def test_rollback_pending_filled_56_no_rollback():
    print("[T4-26] _rollback_pending 遇 56 全成：绝不回滚（防凭空造钱）")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0)
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260801",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0,
                                     "entry_date": "20260801"}
    ST._pending_orders["600000.SH"] = info
    C.order_data["600000.SH"] = make_order("600000.SH", status=56, traded=1000)
    ok = ST._rollback_pending(C, "600000.SH", info)
    check("全成时回滚返回 True（已收尾）", ok is True)
    check("全成不返现（现金未虚增）", ST._cash == 90000.0)
    check("全成保留持仓", ST._holdings["600000.SH"]["volume"] == 1000)
    check("全成不标记 rolled_back", not ST._today_orders["600000.SH"].get("rolled_back"))
    check("全成移出跟踪", "600000.SH" not in ST._pending_orders)


def test_rollback_buy_partial_filled():
    print("[T4-27] 买入 1000 成交 400 后部撤：保留400持仓，只退600现金")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0)
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260801",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0,
                                     "entry_date": "20260801"}
    ST._pending_orders["600000.SH"] = info
    # 54 部撤，累计成交 400
    C.order_data["600000.SH"] = make_order("600000.SH", status=54, traded=400, volume=1000)
    ok = ST._rollback_pending(C, "600000.SH", info)
    check("部撤后回滚完成", ok is True)
    check("持仓只保留已成交 400 股", ST._holdings["600000.SH"]["volume"] == 400)
    check("现金只退未成交 600 段 = +6000", ST._cash == 96000.0)
    check("已成交段不返现（关键红线）", ST._cash != 100000.0)
    check("回滚记录 filled 供对账校准",
          ST._today_orders["600000.SH"].get("rollback_filled") == 400)


def test_rollback_sell_partial_filled():
    print("[T4-28] 卖出 1000 成交 400 后部撤：恢复600持仓，只保留400真实回笼")
    reset(C)
    info = _mk_pending("sell", 1000, 10.0, entry_price=9.0)
    ST._holdings = {}
    ST._cash = 110000.0  # 卖出估算已全额回笼
    ST._today_orders["600000.SH"] = {"dir": "sell", "amount": 1000, "price": 10.0,
                                     "entry_price": 9.0, "entry_date": "20260801"}
    ST._pending_orders["600000.SH"] = info
    C.order_data["600000.SH"] = make_order("600000.SH", opt="卖出", status=54,
                                           traded=400, volume=1000)
    ok = ST._rollback_pending(C, "600000.SH", info)
    check("部撤后回滚完成", ok is True)
    check("恢复未成交 600 股持仓", ST._holdings["600000.SH"]["volume"] == 600)
    check("只扣回已成交 400 段 = 110000-4000", ST._cash == 106000.0)
    check("未成交段的钱未被扣回（关键红线）", ST._cash != 100000.0)


def test_partial_rollback_then_reconcile_no_double_count():
    print("[T4-29] 部分成交回滚后再对账：现金不重复冲销（只校准价差）")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0)
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260801",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0,
                                     "entry_date": "20260801"}
    ST._pending_orders["600000.SH"] = info
    C.order_data["600000.SH"] = make_order("600000.SH", status=54, traded=400, volume=1000)
    ST._rollback_pending(C, "600000.SH", info)
    cash_after_rollback = ST._cash
    # 真实成交：400 股 @ 10.5（估算价 10.0 有 0.5 滑点）
    C.deals = [make_deal("600000.SH", "买入", 400.0, 10.5)]
    ST._reconcile(C)
    # 期望：96000 - (4200 - 400*10) = 96000 - 200 = 95800
    check("对账只校准价差 200（不整笔冲 4200）", abs(ST._cash - 95800.0) < 0.01)
    check("对账现金不为 90000-4200（重复扣钱）", abs(ST._cash - 85800.0) > 0.01)
    check("对账后现金在回滚值附近", abs(ST._cash - cash_after_rollback) < 500.0)
    check("持仓量与真实成交一致", ST._holdings["600000.SH"]["volume"] == 400)
    check("持仓成本校准为真实均价 10.5", abs(ST._holdings["600000.SH"]["cost"] - 10.5) < 1e-6)


def test_calibrate_cumulative_not_incremental():
    print("[T4-30] 累计成交量连查两次不重复扣减（关键红线）")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0)
    ST._pending_orders["600000.SH"] = info
    o = make_order("600000.SH", status=55, traded=400, volume=1000)
    ST._calibrate_filled(info, o)
    check("首次校准剩余 600", info["amount"] == 600)
    check("首次校准总成交 400", info["filled_amount"] == 400)
    # 同一委托再查一次，累计成交仍是 400（不是本 bar 增量）
    ST._calibrate_filled(info, o)
    check("二次校准剩余仍为 600（不被扣成200）", info["amount"] == 600)
    check("二次校准总成交仍为 400（不翻倍）", info["filled_amount"] == 400)
    # 成交推进到 700
    o2 = make_order("600000.SH", status=55, traded=700, volume=1000)
    ST._calibrate_filled(info, o2)
    check("推进到700后剩余300", info["amount"] == 300)
    check("推进后总成交700", info["filled_amount"] == 700)


def test_attempt_carryover_no_mix():
    print("[T4-31] 撤单重下跨 attempt 不混算成交量")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0)
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260801",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0,
                                     "entry_date": "20260801"}
    ST._pending_orders["600000.SH"] = info
    info["cancel_requested"] = True
    info["cancel_action"] = "retry"
    info["cancel_poll"] = 0
    info["cancel_resend"] = ST.CANCEL_RESEND_MAX
    # 旧委托 54 部撤，累计成交 400
    C.order_data["600000.SH"] = make_order("600000.SH", status=54, traded=400, volume=1000)
    ST._confirm_cancel(C, "600000.SH", info, [C.order_data["600000.SH"]])
    check("旧单终态后重下 1 笔", len(C.calls) == 1)
    check("重下数量=剩余600", C.calls and C.calls[0][6] == 600)
    check("结转后 filled_before=400", info.get("filled_before") == 400)
    check("新 attempt 成交清零", info.get("attempt_traded") == 0)
    check("新 attempt 量=600", info.get("attempt_amount") == 600)
    # 新委托成交 200（是本次委托口径，不含旧单 400）
    C.calls = []
    newo = make_order("600000.SH", status=55, traded=200, volume=600, oid="99999",
                      sysid="SYS-999")
    ST._calibrate_filled(info, newo)
    check("新单总成交=400+200=600（不重复计入旧单）", info["filled_amount"] == 600)
    check("新单剩余=400", info["amount"] == 400)


def test_defer_param_removed():
    print("[T4-32] _rollback_pending 已无 defer 旁路（不能绕过终态确认）")
    reset(C)
    import inspect
    try:
        params = inspect.getargspec(ST._rollback_pending)
        names = list(params.args)
    except AttributeError:
        names = list(inspect.signature(ST._rollback_pending).parameters)
    check("不再有 defer 参数", "defer" not in names)
    info = _mk_pending("buy", 1000, 10.0)
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260801",
                                 "peak_close": 10.0, "hold_days": 0}
    ST._cash = 90000.0
    ST._pending_orders["600000.SH"] = info
    # 查不到单 -> 隔离而非回滚
    C.order_data = {}
    ST._rollback_pending(C, "600000.SH", info)
    check("查不到单不返现", ST._cash == 90000.0)
    check("查不到单保持跟踪待对账", "600000.SH" in ST._pending_orders)


def test_find_order_ambiguous_returns_none():
    print("[T4-33] 同代码同方向多候选且均非本策略：歧义返回None（不误撤他人单）")
    reset(C)
    o1 = make_order("600000.SH", oid="1", sysid="S1", remark="OTHER")
    o2 = make_order("600000.SH", oid="2", sysid="S2", remark="")
    got = ST._find_order([o1, o2], "600000.SH", "buy")
    check("多候选歧义返回 None", got is None)
    o3 = make_order("600000.SH", oid="3", sysid="S3", remark="P13H529")
    got2 = ST._find_order([o1, o2, o3], "600000.SH", "buy")
    check("含本策略 remark 时优先选中", got2 is o3)
    got3 = ST._find_order([make_order("600000.SH", oid="9", remark="X")], "600000.SH", "sell")
    check("方向不匹配返回 None", got3 is None)


# ==================== 精确委托号（attempt ref）契约 ====================
def test_buy_binds_passorder_order_id():
    print("[T4-40] 初始买入绑定 passorder 有效委托号（异步返回 0 时归一为空）")
    reset(C)
    C.set_price("600000.SH", 10.0)
    ST._execute_buy(C, "600000.SH")
    info = ST._pending_orders.get("600000.SH")
    check("买入已登记 pending", info is not None)
    check("attempt_order_id = 有效递增单号", info.get("attempt_order_id") == "90001")
    check("新 attempt 不带残留 sysid", info.get("attempt_sysid") == "")

    reset(C)
    C.set_price("600000.SH", 10.0)
    C.passorder_ret_zero = True
    ST._execute_buy(C, "600000.SH")
    info2 = ST._pending_orders.get("600000.SH")
    check("passorder 返 0 -> 委托号归一为空串（不拿 0 去匹配）",
          info2.get("attempt_order_id") == "")
    check("返 0 时仍兼容旧 pending（字段存在不抛异常）",
          "attempt_order_id" in info2 and "attempt_sysid" in info2)


def test_sell_binds_passorder_order_id():
    print("[T4-41] 初始卖出绑定 passorder 有效委托号")
    reset(C)
    buy_code("600000.SH", 10.0, 1000, cost=10.0)
    C.set_price("600000.SH", 9.0)
    ST._execute_sell(C, "600000.SH", "止损")
    info = ST._pending_orders.get("600000.SH")
    check("卖出已登记 pending", info is not None)
    check("卖出 attempt_order_id = 有效单号", info.get("attempt_order_id") == "90001")
    check("卖出方向标记为 sell", info.get("type") == "sell")


def test_retry_binds_new_attempt_order_id():
    print("[T4-42] 撤单终态确认后重下：绑定**新的** attempt 委托号")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0, retries=1, attempt_order_id="70000",
                       attempt_sysid="S-70000",
                       cancel_fields={"cancel_requested": True,
                                      "cancel_action": "retry",
                                      "cancel_sent": True})
    ST._pending_orders["600000.SH"] = info
    # 旧单已撤（54），本 attempt 成交 0 -> 剩余 1000 可全量重下
    o = make_order("600000.SH", opt="买入", oid="70000", sysid="S-70000",
                   status=54, traded=0, volume=1000)
    ST._confirm_cancel(C, "600000.SH", info, [o])
    check("重下后仍在跟踪", "600000.SH" in ST._pending_orders)
    check("新 attempt_order_id 覆盖旧单号", info.get("attempt_order_id") == "90001")
    check("旧单号未被串用", info.get("attempt_order_id") != "70000")
    check("重下笔数=1（撤单+重下各一次）", len(C.calls) == 1 and C.calls[0][0] == 23)


def test_begin_attempt_clears_ids_and_cancel_fields():
    print("[T4-43] _begin_attempt 清空旧单号与全部撤单状态字段")
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="70000",
                       attempt_sysid="S-70000", attempt_traded=400, filled_before=0,
                       cancel_fields={"cancel_requested": True, "cancel_action": "retry",
                                      "cancel_poll": 3, "cancel_resend": 2,
                                      "cancel_unconfirmed": True, "cancel_sent": True})
    ST._begin_attempt(info, 600)
    check("attempt_traded 结转进 filled_before", info["filled_before"] == 400)
    check("新 attempt 成交量归零", info["attempt_traded"] == 0)
    check("attempt_amount=新委托量", info["attempt_amount"] == 600)
    check("旧 attempt_order_id 已清空", info["attempt_order_id"] == "")
    check("旧 attempt_sysid 已清空", info["attempt_sysid"] == "")
    for f in ST._CANCEL_FIELDS:
        check("撤单字段 %s 已清空" % f, f not in info)


def test_find_pending_order_exact_oid_wins():
    print("[T4-44] 同代码同方向多单：按精确 oid 命中本 attempt（不靠 remark 猜）")
    reset(C)
    old_o = make_order("600000.SH", opt="买入", oid="70000", sysid="S-70000",
                       status=54, traded=1000, remark="P13H529", volume=1000)
    new_o = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                       status=None, traded=0, remark="P13H529", volume=1000)
    orders = [old_o, new_o]
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001")
    got = ST._find_pending_order(orders, "600000.SH", info)
    check("精确 oid 命中新 attempt", got is new_o)
    check("命中后回填 attempt_sysid", info.get("attempt_sysid") == "S-90001")

    info2 = _mk_pending("buy", 1000, 10.0, attempt_order_id="70000")
    got2 = ST._find_pending_order(orders, "600000.SH", info2)
    check("精确 oid 命中历史 attempt（旧单号也精确）", got2 is old_o)


def test_find_pending_order_exact_sysid_no_cross_namespace():
    print("[T4-45] 只拿到 sysid 时按 sysid 命名空间精确命中（同值不跨命名空间）")
    reset(C)
    o1 = make_order("600000.SH", opt="买入", oid="55555", sysid="S-90001",
                    status=None, traded=0, remark="", volume=1000)
    o2 = make_order("600000.SH", opt="买入", oid="90001", sysid="S-77777",
                    status=None, traded=0, remark="", volume=1000)
    orders = [o1, o2]
    # pending 只握有 sysid=S-90001（柜台合同号），绝不能命中 oid=90001 的另一张单
    info = _mk_pending("buy", 1000, 10.0, attempt_sysid="S-90001")
    got = ST._find_pending_order(orders, "600000.SH", info)
    check("sysid 命名空间精确命中", got is o1)
    check("未跨命名空间误命中同值 oid", got is not o2)

    # pending 只握有 oid=90001，必须命中 o2（oid 空间）
    info2 = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001")
    got2 = ST._find_pending_order(orders, "600000.SH", info2)
    check("oid 命名空间精确命中", got2 is o2)


def test_find_pending_order_no_ref_multi_candidate_no_cancel():
    print("[T4-46] 无单号且多候选歧义 -> 不误撤（交由上层隔离）")
    reset(C)
    o1 = make_order("600000.SH", opt="买入", oid="70000", sysid="S-1",
                    status=None, remark="OTHER", volume=1000)
    o2 = make_order("600000.SH", opt="买入", oid="70001", sysid="S-2",
                    status=None, remark="", volume=1000)
    info = _mk_pending("buy", 1000, 10.0)
    got = ST._find_pending_order([o1, o2], "600000.SH", info)
    check("歧义返回 None", got is None)
    ST._pending_orders["600000.SH"] = info
    ST._retry_pending(C, "600000.SH", info)
    check("歧义下单不发出撤单（防误撤他人单）", len(C.cancels) == 0)
    check("歧义下单置隔离态", info.get("cancel_unconfirmed") is True)
    check("歧义下单不重下", len(C.calls) == 0)


# ==================== deal 领先快照与单调口径 ====================
def test_deal_leads_snapshot_upward_only():
    print("[T4-50] deal 成交领先委托快照：只上调成交量（快照回退不下调）")
    reset(C)
    o = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                   status=55, traded=0, volume=1000)
    # 柜台已成交 400，但委托快照仍显示 0（撤单瞬间常见滞后）
    C.deals.append(make_deal("600000.SH", opt="买入", vol=400.0, price=10.5,
                             oid="90001", sysid="S-90001"))
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                       attempt_sysid="S-90001")
    filled = ST._calibrate_filled(info, o, "600000.SH")
    check("deal 领先 -> attempt_traded 上调到 400", info["attempt_traded"] == 400)
    check("filled_amount=400", filled == 400)
    check("剩余可重下量=600", info["amount"] == 600)

    # 后续快照回退到更小值（换批次快照），不得下调已认成交量
    o2 = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                    status=55, traded=0, volume=1000)
    C.deals = []
    ST._calibrate_filled(info, o2, "600000.SH")
    check("快照回退不下调（仍持 400）", info["attempt_traded"] == 400)
    check("快照回退后剩余仍 600", info["amount"] == 600)


def test_deal_not_leak_across_attempts():
    print("[T4-51] deal 不得串入同代码其他 attempt（attempt 口径隔离）")
    reset(C)
    cur = make_order("600000.SH", opt="买入", oid="90002", sysid="S-90002",
                     status=54, traded=0, volume=600)
    # 同代码同方向上一轮 attempt 的成交，必须排除
    C.deals.append(make_deal("600000.SH", opt="买入", vol=400.0, price=10.0,
                             oid="90001", sysid="S-90001"))
    C.deals.append(make_deal("600000.SH", opt="买入", vol=600.0, price=10.0,
                             oid="90002", sysid="S-90002"))
    info = _mk_pending("buy", 600, 10.0, attempt_order_id="90002",
                       attempt_sysid="S-90002", attempt_amount=600,
                       filled_before=400)
    ST._calibrate_filled(info, cur, "600000.SH")
    check("只认本 attempt 的 600 股成交", info["attempt_traded"] == 600)
    check("历史 400 由 filled_before 承担（不串味）", info["filled_amount"] == 1000)


def test_deal_id_matching_same_namespace_only():
    print("[T4-52] deal 聚合只在同命名空间求交集：同字段不对称可命中，跨序列同值不互认")
    reset(C)
    # 委托只回填了 sysid（oid 字段为 None），deal 也只带 sysid
    o = make_order("600000.SH", opt="买入", oid=None, sysid="S-90001",
                   status=54, traded=0, volume=1000)
    C.deals.append(make_deal("600000.SH", opt="买入", vol=500.0, price=10.0,
                             sysid="S-90001"))
    info = _mk_pending("buy", 1000, 10.0, attempt_sysid="S-90001")
    ST._calibrate_filled(info, o, "600000.SH")
    check("同命名空间(sysid)：双方字段不对称也能命中", info["attempt_traded"] == 500)

    # 委托两个字段都有，deal 只有 oid -> 靠共同的 oid 命中
    reset(C)
    o2 = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                    status=54, traded=0, volume=1000)
    C.deals.append(make_deal("600000.SH", opt="买入", vol=300.0, price=10.0,
                             oid="90001"))
    info2 = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                        attempt_sysid="S-90001")
    ST._calibrate_filled(info2, o2, "600000.SH")
    check("同命名空间(oid)：deal 只带 oid 也能命中两字段齐全的委托",
          info2["attempt_traded"] == 300)

    # 反向红线：委托只有 sysid=S-77，deal 只有 oid=S-77（数值碰巧相同）-> 不互认
    reset(C)
    o3 = make_order("600000.SH", opt="买入", oid=None, sysid="S-77",
                    status=54, traded=0, volume=1000)
    C.deals.append(make_deal("600000.SH", opt="买入", vol=999.0, price=10.0,
                             oid="S-77"))
    info3 = _mk_pending("buy", 1000, 10.0, attempt_sysid="S-77")
    ST._calibrate_filled(info3, o3, "600000.SH")
    check("跨命名空间(sysid vs oid)同值不互认，不虚增成交量",
          info3["attempt_traded"] == 0)


def test_deal_capped_by_attempt_amount():
    print("[T4-53] deal 成交量异常（>委托量）按满额封顶，不虚增现金")
    reset(C)
    o = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                   status=54, traded=0, volume=1000)
    C.deals.append(make_deal("600000.SH", opt="买入", vol=1500.0, price=10.0,
                             oid="90001", sysid="S-90001"))
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                       attempt_sysid="S-90001")
    ST._calibrate_filled(info, o, "600000.SH")
    check("deal 超量按 attempt_amount 封顶", info["attempt_traded"] == 1000)
    check("剩余量不为负", info["amount"] == 0)


# ==================== 撤单三态可观测性 ====================
def test_cancel_states_on_retry_paths():
    print("[T4-60] 撤单三态：不可撤/发不出/已发待确认 三种可观测结果")
    # ① 查不到原单 -> cancel_sent=False + 隔离
    reset(C)
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001")
    ST._pending_orders["600000.SH"] = info
    C.order_data = {}
    ST._retry_pending(C, "600000.SH", info)
    check("①查不到原单: cancel_sent=False", info.get("cancel_sent") is False)
    check("①查不到原单: cancel_requested=True", info.get("cancel_requested") is True)
    check("①查不到原单: cancel_unconfirmed=True", info.get("cancel_unconfirmed") is True)
    check("①查不到原单: 未发出撤单", len(C.cancels) == 0)
    check("①查不到原单: 不重下", len(C.calls) == 0)

    # ② 撤单接口失败（不可撤）-> cancel_sent=False + 隔离
    reset(C)
    info2 = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001")
    ST._pending_orders["600000.SH"] = info2
    C.order_data["600000.SH"] = make_order("600000.SH", opt="买入", oid="90001",
                                          sysid="S-90001", status=None, volume=1000)
    C.not_cancelable.add("S-90001")
    ST._retry_pending(C, "600000.SH", info2)
    check("②不可撤: cancel_sent=False", info2.get("cancel_sent") is False)
    check("②不可撤: cancel_unconfirmed=True", info2.get("cancel_unconfirmed") is True)
    check("②不可撤: 不重下（防叠单）", len(C.calls) == 0)

    # ③ 撤单已发出但未终态 -> cancel_sent=True，且不重下
    reset(C)
    info3 = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001")
    ST._pending_orders["600000.SH"] = info3
    C.order_data["600000.SH"] = make_order("600000.SH", opt="买入", oid="90001",
                                          sysid="S-90001", status=None, volume=1000)
    ok = ST._retry_pending(C, "600000.SH", info3)
    check("③撤单已发: 返回 True", ok is True)
    check("③撤单已发: cancel_sent=True", info3.get("cancel_sent") is True)
    check("③撤单已发: cancel_requested=True", info3.get("cancel_requested") is True)
    check("③撤单已发: 无隔离标志", not info3.get("cancel_unconfirmed"))
    check("③撤单已发: 仍不重下（等终态）", len(C.calls) == 0)
    check("③撤单已发: 轮询计数归零", info3.get("cancel_poll") == 0)


def test_confirm_cancel_resend_clears_unconfirmed():
    print("[T4-61] 确认态重发撤单成功 -> 清除旧隔离标志；失败 -> 维持隔离")
    # ① 重发成功：清除旧 cancel_unconfirmed
    reset(C)
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                       cancel_fields={"cancel_requested": True, "cancel_action": "retry",
                                      "cancel_poll": 1, "cancel_resend": 0,
                                      "cancel_unconfirmed": True})
    ST._pending_orders["600000.SH"] = info
    o = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                   status=None, traded=0, volume=1000)
    ST._confirm_cancel(C, "600000.SH", info, [o])
    check("①重发成功: cancel_sent=True", info.get("cancel_sent") is True)
    check("①重发成功: 旧隔离标志已清除", not info.get("cancel_unconfirmed"))
    check("①重发成功: 仍在确认态（未重下）", info.get("cancel_requested") is True)
    check("①重发成功: 未重下", len(C.calls) == 0)

    # ② 重发失败（不可撤）：维持隔离
    reset(C)
    info2 = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                        cancel_fields={"cancel_requested": True, "cancel_action": "retry",
                                       "cancel_poll": 1, "cancel_resend": 0})
    ST._pending_orders["600000.SH"] = info2
    o2 = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                    status=None, traded=0, volume=1000)
    C.not_cancelable.add("S-90001")
    ST._confirm_cancel(C, "600000.SH", info2, [o2])
    check("②重发失败: cancel_sent=False", info2.get("cancel_sent") is False)
    check("②重发失败: 隔离标志置位", info2.get("cancel_unconfirmed") is True)
    check("②重发失败: 未重下", len(C.calls) == 0)


def test_confirm_cancel_resend_cap_then_quarantine():
    print("[T4-62] 重发封顶且仍不终态 -> 隔离，绝不重下")
    reset(C)
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                       cancel_fields={"cancel_requested": True, "cancel_action": "retry",
                                      "cancel_poll": 1, "cancel_resend": ST.CANCEL_RESEND_MAX,
                                      "cancel_sent": True})
    ST._pending_orders["600000.SH"] = info
    o = make_order("600000.SH", opt="买入", oid="90001", sysid="S-90001",
                   status=None, traded=0, volume=1000)
    C.not_cancelable.add("S-90001")
    # 轮询次数拉到封顶
    info["cancel_poll"] = ST.CANCEL_CONFIRM_MAX_POLL
    ST._confirm_cancel(C, "600000.SH", info, [o])
    check("封顶+轮询封顶: 隔离标志置位", info.get("cancel_unconfirmed") is True)
    check("封顶+轮询封顶: 仍在跟踪等对账", "600000.SH" in ST._pending_orders)
    check("封顶+轮询封顶: 绝不重下", len(C.calls) == 0)


def test_reconcile_reports_quarantined_pending():
    print("[T4-63] 收盘对账显式消费隔离态挂单（落日志后清空）")
    reset(C)
    ST._cash = 90000.0
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0,
                                     "entry_date": "20260801"}
    ST._holdings["600000.SH"] = {"volume": 1000, "cost": 10.0, "entry_date": "20260801",
                                 "peak_close": 10.0, "hold_days": 0}
    info = _mk_pending("buy", 1000, 10.0, attempt_order_id="90001",
                       cancel_fields={"cancel_requested": True, "cancel_action": "retry",
                                      "cancel_poll": 6, "cancel_resend": 3,
                                      "cancel_unconfirmed": True, "cancel_sent": False})
    ST._pending_orders["600000.SH"] = info
    log_path = os.path.join(SANDBOX, "strategy_log_p13_huang529.txt")
    if os.path.exists(log_path):
        os.remove(log_path)
    ST._reconcile(C)
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    check("隔离态挂单在对账日志中显式落地", "隔离态挂单" in content and "600000.SH" in content)
    check("日志含撤单已发标记 False", "撤单已发=False" in content)
    check("对账后 pending 已清空", len(ST._pending_orders) == 0)
    check("对账只结清 _today_orders（不重复改持仓）", len(ST._today_orders) == 0)
    check("持仓按账户无货撤销（1000股虚拟仓清零）", "600000.SH" not in ST._holdings)


def test_idempotent_bar():
    print("[T4-15] 重复 bar 幂等（当日决策只执行一次）")
    reset(C)
    C.set_price("600000.SH", 10.0)
    write_signal([("600000.SH", 2.0, 1)])
    ST._decision(C, C.now)
    n1 = len(ST._holdings)
    ST._decision(C, C.now)
    check("同日不重复买入", len(ST._holdings) == n1)


def test_csv_export():
    print("[T4-16] CSV 导出")
    reset(C)
    buy_code("600000.SH", 10.0, 1000, cost=10.0)
    ST._export_daily_csv(C, "20260818")
    pos_path = os.path.join(SANDBOX, "p13_huang529_holdings_20260818.csv")
    check("持仓 CSV 已导出", os.path.exists(pos_path))
    with open(pos_path, "r", encoding="utf-8") as f:
        content = f.read()
    check("CSV 含 600000.SH", "600000.SH" in content)


def main():
    global C
    print("=== P13 黄氏529 MOCK 测试（T4） 沙箱=%s ===" % SANDBOX)
    load_module()
    C = MockC()
    for p in os.listdir(SANDBOX):
        os.remove(os.path.join(SANDBOX, p))
    test_signal_normal()
    test_signal_missing()
    test_signal_empty()
    test_slot_and_cap()
    test_cash_insufficient()
    test_stop_loss()
    test_trailing_peak()
    test_expiry()
    test_t_plus_1()
    test_limit_up_no_buy()
    test_limit_down_pending()
    test_state_restore()
    test_reconcile_three_state()
    test_rollback_cancel()
    test_rollback_after_cancel_confirmed()
    test_rollback_orphan_order_quarantine()
    test_cancel_uses_order_id_when_no_sysid()
    test_cancel_not_cancelable_blocks_retry()
    test_retry_pending_two_phase()
    test_confirm_cancel_filled_no_reorder()
    test_confirm_cancel_part_filled_reorder_remainder()
    test_confirm_cancel_unconfirmed_quarantine()
    test_order_status_terminal_matrix()
    test_rollback_pending_filled_56_no_rollback()
    test_rollback_buy_partial_filled()
    test_rollback_sell_partial_filled()
    test_partial_rollback_then_reconcile_no_double_count()
    test_calibrate_cumulative_not_incremental()
    test_attempt_carryover_no_mix()
    test_defer_param_removed()
    test_find_order_ambiguous_returns_none()
    test_buy_binds_passorder_order_id()
    test_sell_binds_passorder_order_id()
    test_retry_binds_new_attempt_order_id()
    test_begin_attempt_clears_ids_and_cancel_fields()
    test_find_pending_order_exact_oid_wins()
    test_find_pending_order_exact_sysid_no_cross_namespace()
    test_find_pending_order_no_ref_multi_candidate_no_cancel()
    test_deal_leads_snapshot_upward_only()
    test_deal_not_leak_across_attempts()
    test_deal_id_matching_same_namespace_only()
    test_deal_capped_by_attempt_amount()
    test_cancel_states_on_retry_paths()
    test_confirm_cancel_resend_clears_unconfirmed()
    test_confirm_cancel_resend_cap_then_quarantine()
    test_reconcile_reports_quarantined_pending()
    test_idempotent_bar()
    test_csv_export()
    print("=== 结果: %d PASS / %d FAIL ===" % (PASS, FAIL))
    if FAILED:
        print("FAILED: %s" % ", ".join(FAILED))
    shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()