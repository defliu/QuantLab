# coding=utf-8
"""R9 验证：ATR equalweight build ROE 空结果 fail-open（2026-08-15 诚哥拍板）。

场景1：xtdata.get_financial_data 整批返回空 -> 不得过滤全部候选（防整季空仓）。
场景2：部分股票有 ROE 数据 -> 个股级 None/<=0 仍被过滤（门控保留）。

运行：python research/tests/test_roe_r9_failopen.py（纯内存 mock，无需 miniQMT）
"""
import sys
import types
from datetime import datetime

import numpy as np
import pandas as pd

BUILD = r"D:/QuantLab/projects/Project_ATR_lowvol/build/strategy_atr_lowvol_equalweight.py"


def make_kline(count=260, start=10.0, drift=0.0006, vol=0.006, seed=0):
    """平稳上行低波动 K 线：ATR% 低、12-1月动量>0、换手率 1-8%。"""
    rng = np.random.RandomState(seed)
    rets = drift + rng.randn(count) * vol
    closes = start * np.exp(np.cumsum(rets))
    dates = pd.date_range(end=datetime.now(), periods=count, freq="B")
    highs = closes * (1 + np.abs(rng.randn(count)) * 0.004)
    lows = closes * (1 - np.abs(rng.randn(count)) * 0.004)
    return pd.DataFrame({
        "open": closes * (1 + rng.randn(count) * 0.003),
        "high": highs,
        "low": lows,
        "close": closes,
        "amount": closes * rng.randint(5000, 50000, count) * 100,
        "turnover_rate": np.full(count, 0.03),  # 小数0.03=3% 换手（build 内 ×100 后进 [1,8]）
    }, index=dates)


class MockC(object):
    def __init__(self, data):
        self._data = data
        self.do_back_test = False
        self.do_backtest = False

    def get_stock_list_in_sector(self, sec):
        if sec in ("风险警示板", "ST"):
            return []
        return list(self._data.keys())

    def get_market_data_ex(self, stock_code=None, period="1d", count=0, **kw):
        return {c: df.tail(count) for c, df in self._data.items() if c in self._data}

    def get_turnover_rate(self, *a, **k):
        raise AttributeError("no get_turnover_rate (local)")

    def get_stock_basic_info(self, code):
        return {"name": code}

    def get_current_time(self):
        return datetime.now()

    def get_trade_detail_data(self, *a, **k):
        return []

    def passorder(self, *a, **k):
        raise RuntimeError("LOCAL: passorder must not be called")


def load_build():
    # 注入 xtdata stub（build 内 `import xtdata` 会取到它）
    stub = types.ModuleType("xtdata")
    stub.get_financial_data = lambda *a, **k: {}
    sys.modules["xtdata"] = stub
    raw = open(BUILD, "rb").read().decode("gbk")
    ns = {}
    exec(compile(raw, "atr_ew_build", "exec"), ns)
    return ns


def run_screening(ns, C, fin_result):
    ns["_g_my_codes"] = {}
    ns["_g_hold_pool_cache"] = None
    ns["_g_hold_pool_cache_date"] = ""
    ns["_g_roe_cache"] = {}
    ns["_g_roe_api_ok"] = None
    sys.modules["xtdata"].get_financial_data = lambda *a, **k: fin_result
    return ns["_run_screening"](C)


def main():
    codes = ["60000%d.SH" % i for i in range(1, 11)]
    data = {c: make_kline(seed=i) for i, c in enumerate(codes)}
    C = MockC(data)
    ns = load_build()

    # 场景1：整批空结果 -> fail-open（R9 修复后不得整季空仓）
    sel_empty = run_screening(ns, C, {})
    print("[R9-场景1] 财务空结果: 入选 %d 只 (fail-open, 应>0)" % len(sel_empty))
    assert len(sel_empty) > 0, "R9 修复失败：空结果仍过滤全部候选！"

    # 场景2：部分 ROE 数据 -> 个股级 None/<=0 仍过滤
    roe_map = {c: (12.0 if i % 2 == 0 else None) for i, c in enumerate(codes)}
    sel_partial = run_screening(ns, C, roe_map)
    print("[R9-场景2] 部分ROE(None过滤): 入选 %d 只 (应只含 ROE>0 的票)" % len(sel_partial))
    assert all(roe_map.get(c) and roe_map[c] > 0 for c in sel_partial), \
        "ROE<=0/None 的票未被过滤！门控被破坏"
    print("  -> 入选均来自 ROE>0 的票:", sorted(sel_partial))
    assert len(sel_partial) <= sum(1 for v in roe_map.values() if v and v > 0), \
        "入选数应 ≤ ROE>0 的票数"
    print("  [PASS] 场景2：部分数据时个股级门控仍生效")

    print("\n[R9] 两个场景全部 PASS")


if __name__ == "__main__":
    main()
