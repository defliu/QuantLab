# coding: utf-8
"""P13 黄氏529 本地冒烟验证（T6）

验证项：
  1) 信号 CSV 读取通（真实 D:/QMT_POOL，最新 T-1 日）
  2) 候选数合理（合成 16 只 -> 补槽 12 只、1/12 上限）
  3) fail-open 生效（信号目录缺失 -> 空列表、当日不买入）
  4) 不实盘下单（passorder 全程 mock，仅记录参数不触网）

若 xtquant 可用且 QMT mini 客户端在跑 -> 额外用 LocalContext 做真实行情限价判定；
否则用 NullC 行情桩（行情不可得 -> 策略 fail-open，符合设计）。

用法:  D:/Python311/python.exe local_validate.py
  或  <miniqmt venv>/python.exe local_validate.py
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time

PROJ_DIR = os.path.dirname(os.path.abspath(__file__))
QUANTLAB = os.path.dirname(os.path.dirname(PROJ_DIR))
sys.path.insert(0, PROJ_DIR)
sys.path.insert(0, QUANTLAB)

from broker.local_context import load_strategy_source

BUILD = os.path.join(PROJ_DIR, "build", "strategy_huang529.py")
REAL_POOL = "D:/QMT_POOL"

# ============ Mock 下单（不触网） ============
ORDERS = []


def mock_passorder(*args):
    ORDERS.append(args)
    return 0


def mock_get_trade_detail_data(acct, typ, kind):
    return []


# ============ C 桩 ============
class NullC(object):
    """行情桩：可注入 price_map；取不到行情返回空 -> 策略 fail-open。"""

    def __init__(self):
        self.prices = {}

    def get_current_time(self):
        from datetime import datetime
        return datetime.now()

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
            d = {}
            for f in fields:
                if f == "close":
                    d[f] = [p]
                elif f == "pre_close":
                    d[f] = [p]
                elif f == "open":
                    d[f] = [p]
                elif f == "volume":
                    d[f] = [1000]
                else:
                    d[f] = [0.0]
            out[c] = pd.DataFrame(d)
        return out

    def get_tick_timetag(self):
        return 0

    def get_bar_timetag(self, bp):
        return 0


def main():
    t0 = time.time()
    print("=== P13 黄氏529 本地冒烟验证（T6） ===")

    # 0) 加载构建产物（GBK 单文件 -> UTF-8）
    mod = load_strategy_source(BUILD)
    mod.passorder = mock_passorder
    mod.get_trade_detail_data = mock_get_trade_detail_data
    print("BUILD_TAG =", mod.BUILD_TAG)
    print("N_HOLD=%d MAX_SINGLE_PCT=%.4f STOP_LOSS=%.2f MAX_HOLDING_DAYS=%d trail=%s" % (
        mod.N_HOLD, mod.MAX_SINGLE_PCT, mod.STOP_LOSS, mod.MAX_HOLDING_DAYS, mod.TRAILING_STOP))

    # 1) 真实信号池：读取最新 T-1 信号
    sig_date = mod._latest_signal_date()
    print("\n[1] 真实信号池最新日 =", sig_date, "（< 今日）")
    signals = mod._load_signal_csv()
    print("    真实池候选数 = %d（fail-open=%s）" % (len(signals), len(signals) == 0))
    assert sig_date is not None or len(signals) == 0, "有信号日但读取异常"

    # 2) 合成候选：验证补槽 12 只 + 1/12 上限 + 不实盘下单
    print("\n[2] 合成 16 只信号 -> 补槽 12 只（1/12 上限）")
    sandbox = tempfile.mkdtemp(prefix="p13_smoke_")
    mod.SIGNAL_DIR = sandbox
    today_ds = mod._latest_signal_date() or "20260801"
    with open(os.path.join(sandbox, mod.SIGNAL_PREFIX + "20260817.csv"), "w", encoding="utf-8") as f:
        f.write("ts_code,atr_pct,rank\n")
        for i in range(16):
            f.write("600%03d.SH,%.1f,%d\n" % (i, 2.0 + i * 0.5, i + 1))
    C = NullC()
    for i in range(16):
        C.prices["600%03d.SH" % i] = 10.0
    ORDERS[:] = []
    mod._cash = mod.CAPITAL_BASE
    mod._holdings = {}
    mod._pending_orders = {}
    mod._today_orders = {}
    mod._suspended_sells = []
    mod._last_decision_date = ""
    mod._decision(C, C.get_current_time())
    n_buy = sum(1 for o in ORDERS if o[0] == 23)
    print("    买入委托 = %d（期望 12），持仓 = %d" % (n_buy, len(mod._holdings)))
    assert n_buy == 12, "补槽数量不对: %d" % n_buy
    for code, info in mod._holdings.items():
        assert info["volume"] * 10.0 <= mod.CAPITAL_BASE / 12.0 + 1, "单票超限: %s" % code
    print("    单票市值 <= 总资产/12  OK")

    # 3) fail-open：信号目录缺失
    print("\n[3] fail-open：信号目录缺失")
    mod.SIGNAL_DIR = "D:/QMT_POOL_NONEXIST_XYZ"
    sigs = mod._load_signal_csv()
    print("    返回 =", sigs)
    assert sigs == [], "fail-open 应返回空列表"
    print("    OK")

    # 4) 真实池 fail-open 复查（恢复 SIGNAL_DIR）
    print("\n[4] 恢复真实信号目录")
    mod.SIGNAL_DIR = REAL_POOL
    real_date = mod._latest_signal_date()
    real_sigs = mod._load_signal_csv()
    print("    最新日=%s 候选=%d" % (real_date, len(real_sigs)))

    # 5) 若 xtquant 可用且 miniQMT 在跑，做真实行情限价判定（可选）
    try:
        import xtquant  # noqa
        from broker.local_context import connect_data, LocalContext
        connect_data()
        lc = LocalContext()
        codes = ["600000.SH", "000001.SZ"]
        quotes = lc.get_market_data_ex(stock_code=codes, period="1d", count=1)
        ok = sum(1 for c in codes if quotes.get(c) is not None and len(quotes.get(c)) > 0)
        print("\n[5] xtquant 实时行情可用：%d/%d 有行情" % (ok, len(codes)))
    except Exception as e:
        print("\n[5] xtquant/miniQMT 不可用（跳过实时行情判定）: %s" % e)

    shutil.rmtree(sandbox, ignore_errors=True)
    print("\n=== T6 冒烟通过（%.1fs），全程未触发真实下单（ORDERS=%d 条 mock 记录） ===" % (
        time.time() - t0, len(ORDERS)))


if __name__ == "__main__":
    main()