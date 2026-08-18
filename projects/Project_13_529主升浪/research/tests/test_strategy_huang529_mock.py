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
        self.order_data = {}      # code -> order 对象 dict（反查）
        self.positions = {}       # 账户 position: code -> volume
        self.now = now or datetime(2026, 8, 18, 9, 40, 0)

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
        self.calls.append(args)
        return 0

    def get_trade_detail_data(self, acct, typ, kind):
        if kind == "order":
            return list(self.order_data.values())
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
    # 清空 MockC 全部状态，避免跨用例污染
    C.calls = []
    C.prices = {}
    C.limit_up = set()
    C.limit_down = set()
    C.suspended = set()
    C.order_data = {}
    C.positions = {}


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


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        FAILED.append(name)
        print("  [FAIL] %s" % name)


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
    print("[T4-14] 回滚先撤活单")
    reset(C)
    ST._today_orders["600000.SH"] = {"dir": "buy", "amount": 1000, "price": 10.0, "entry_date": "20260818"}
    C.order_data["600000.SH"] = type("ORD", (), {
        "m_strInstrumentID": "600000.SH", "m_strOptName": "买入",
        "m_nOrderID": "12345", "m_nVolumeTraded": 0,
        "m_nOrderVolume": 1000, "m_strRemark": "P13H529", "m_strInsertTime": "09:35:00"})()
    info = {"type": "buy", "amount": 1000, "original_amount": 1000, "price": 10.0,
            "time": datetime(2026, 8, 18, 9, 35, 0), "retries": 2}
    ST._rollback_pending(C, "600000.SH", info)
    has_cancel = any(a[0] == 24 and a[5] == "12345" for a in C.calls)
    check("回滚前先撤活单", has_cancel)
    check("买入回滚撤销虚拟持仓", "600000.SH" not in ST._holdings)


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
    test_idempotent_bar()
    test_csv_export()
    print("=== 结果: %d PASS / %d FAIL ===" % (PASS, FAIL))
    if FAILED:
        print("FAILED: %s" % ", ".join(FAILED))
    shutil.rmtree(SANDBOX, ignore_errors=True)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()