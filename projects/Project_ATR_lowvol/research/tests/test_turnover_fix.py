# coding=utf-8
"""
ATR 换手率修复验证测试
验证: get_turnover_rate 不可用时选股不被全杀、条件退出不误判

运行: python -m pytest atr_lowvol/tests/test_turnover_fix.py -v
"""
import sys
import os
import math
import time
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, PropertyMock

import pandas as pd
import numpy as np

# 导入被测试模块（先添加项目根目录到路径）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import importlib

# ============================================================
# Helper: 创建模拟 K 线 DataFrame
# ============================================================
def make_mock_kline(count=60, close_start=10.0, volatility=0.02):
    """生成模拟日K线 DataFrame"""
    np.random.seed(42)
    closes = close_start * (1 + np.random.randn(count) * volatility).cumprod()
    highs = closes * (1 + np.abs(np.random.randn(count)) * 0.015)
    lows = closes * (1 - np.abs(np.random.randn(count)) * 0.015)
    amounts = closes * np.random.randint(5000, 50000, size=count) * 100  # 成交额
    dates = pd.date_range(end=datetime.now(), periods=count, freq='B')
    df = pd.DataFrame({
        'open': closes * (1 + np.random.randn(count) * 0.005),
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': np.random.randint(10000, 100000, size=count),
        'amount': amounts,
    }, index=dates)
    return df


# ============================================================
# Mock QMT Context
# ============================================================
def make_mock_C(turnover_api_available=True, mock_data=None):
    """
    创建模拟 QMT Context 对象 C
    turnover_api_available=False 模拟 get_turnover_rate 不存在
    """
    C = MagicMock()

    if mock_data is None:
        mock_data = {
            '000001.SZ': make_mock_kline(60, 10.0, 0.015),   # 低波动 ATR<6
            '000002.SZ': make_mock_kline(60, 20.0, 0.05),    # 高波动 ATR>6
            '000003.SZ': make_mock_kline(60, 15.0, 0.02),    # 中波动
            '600000.SH': make_mock_kline(60, 8.0, 0.01),     # 低波动
            '600001.SH': make_mock_kline(60, 12.0, 0.03),    # 中波动
        }

    # get_stock_list_in_sector
    C.get_stock_list_in_sector.return_value = list(mock_data.keys())

    # get_market_data_ex
    def mock_get_market_data_ex(stock_code=None, period='1d', count=60, **kwargs):
        result = {}
        for code in stock_code:
            if code in mock_data:
                df = mock_data[code]
                result[code] = df.tail(count)
        return result
    C.get_market_data_ex = mock_get_market_data_ex

    # get_turnover_rate
    if turnover_api_available:
        def mock_get_turnover_rate(codes, start, end):
            df_dict = {}
            for code in codes:
                df_dict[code] = pd.Series(
                    np.random.uniform(0.02, 0.06, size=30),  # 2%-6% 换手率
                    index=pd.date_range(end=datetime.now(), periods=30, freq='B')
                )
            return pd.DataFrame(df_dict)
        C.get_turnover_rate = mock_get_turnover_rate
    else:
        # 模拟 get_turnover_rate 不存在（QMT 策略上下文实际情况）
        def raise_attr_error(*args, **kwargs):
            raise AttributeError("'ContextInfo' object has no attribute 'get_turnover_rate'")
        C.get_turnover_rate = raise_attr_error

    # get_stock_basic_info
    C.get_stock_basic_info.return_value = {'name': 'MockStock'}

    # get_current_time
    now = datetime.now()
    C.get_current_time.return_value = now

    # do_back_test
    C.do_back_test = False

    # get_trade_detail_data - 返回空（无持仓）
    C.get_trade_detail_data.return_value = []

    return C, mock_data


# ============================================================
# 测试类
# ============================================================
class TestATRTurnoverFix:
    """验证换手率修复的三个核心场景"""

    def setup_method(self):
        """每个测试前重新加载模块以重置全局状态"""
        # 清除模块缓存
        for key in list(sys.modules.keys()):
            if 'strategy_atr' in key:
                del sys.modules[key]

    def _reload_module(self, C):
        """加载 strategy_atr 模块并注入 mock C"""
        import atr_lowvol.strategy_atr as strat
        importlib.reload(strat)
        # 注入 mock C 到模块全局
        strat.C = C
        return strat

    # ----------------------------------------------------------
    # 场景 1: get_turnover_rate 不存在 → 选股正常通过
    # ----------------------------------------------------------
    def test_screening_when_turnover_api_missing(self):
        """API 不存在时，选股不被换手率过滤全杀"""
        C, mock_data = make_mock_C(turnover_api_available=False)
        strat = self._reload_module(C)

        # 重置全局状态
        strat._g_my_codes = {}
        strat._g_hold_pool_cache = None
        strat._g_hold_pool_cache_date = ''
        strat._g_turnover_cache = {}

        # 执行选股
        result = strat._run_screening(C)

        # 验证：有候选股票（不被全杀）
        assert len(result) > 0, \
            "get_turnover_rate 不存在时选股返回空！换手率过滤仍在全杀"
        print("  [PASS] 选股返回 %d 只候选 (API不可用)" % len(result))

        # 验证：候选股票确实 ATR% < 6%
        for r in result:
            df = mock_data[r['code']]
            atr_pct = strat._calc_atr_pct(df)
            assert atr_pct < 6.0, \
                "%s ATR%%=%.2f 应 < 6.0" % (r['code'], atr_pct)
        print("  [PASS] 所有候选 ATR%% 均 < 6.0")

        # 验证：换手率字段存在且为 0.0（API不可用时默认值）
        for r in result:
            assert 'turnover' in r
            assert r['turnover'] == 0.0, \
                "API不可用时 turnover 应为 0.0，实际=%.2f" % r['turnover']
        print("  [PASS] 所有候选 turnover=0.0 (API不可用)")

    # ----------------------------------------------------------
    # 场景 2: get_turnover_rate 存在 → 换手率过滤正常工作
    # ----------------------------------------------------------
    def test_screening_when_turnover_api_available(self):
        """API 存在时，换手率过滤正常工作"""
        C, mock_data = make_mock_C(turnover_api_available=True)
        strat = self._reload_module(C)

        strat._g_my_codes = {}
        strat._g_hold_pool_cache = None
        strat._g_hold_pool_cache_date = ''
        strat._g_turnover_cache = {}

        result = strat._run_screening(C)

        # 验证：有候选（换手率在1-8%范围内）
        assert len(result) > 0, \
            "API存在时选股返回空"
        print("  [PASS] 选股返回 %d 只候选 (API可用)" % len(result))

        # 验证：换手率在 1-8% 范围内
        for r in result:
            assert 1.0 <= r['turnover'] <= 8.0, \
                "%s 换手率=%.2f%% 应 1-8%%" % (r['code'], r['turnover'])
        print("  [PASS] 所有候选换手率均在 1-8%% 范围内")

    # ----------------------------------------------------------
    # 场景 3: 条件退出不因换手率 API 不可用而误判
    # ----------------------------------------------------------
    def test_condition_exit_no_false_positive_when_turnover_unavailable(self):
        """API 不可用时，条件退出不应因换手率=0 而触发卖出"""
        C, mock_data = make_mock_C(turnover_api_available=False)
        strat = self._reload_module(C)

        # 模拟持仓：一只 ATR 正常的股票
        code = '000001.SZ'
        strat._g_my_codes = {
            code: {
                'buy_price': 10.0,
                'shares': 1000,
                'peak_price': 10.5,
            }
        }
        # 设置换手率缓存为 -1（表示 API 不可用）
        strat._g_turnover_cache = {code: -1}
        # 注入全市场数据
        strat._g_all_data = mock_data

        # 构建当前价格（= 买入价附近，不触发止损/止盈/移动止盈）
        current_prices = {code: 10.2}  # +2%，在 -8% 止损 和 +20% 止盈之间

        to_sell = strat._evaluate_sells(C, current_prices)

        # 验证：不应因条件失效而卖出（换手率 API 不可用时跳过检查）
        condition_exit_sells = [s for s in to_sell if '条件失效' in s[1]]
        assert len(condition_exit_sells) == 0, \
            "API不可用时条件退出误判！应跳过换手率检查: %s" % condition_exit_sells
        print("  [PASS] 条件退出未误判（0条'条件失效'卖出）")

        # 验证：整体不应有任何卖出（价格在正常范围）
        assert len(to_sell) == 0, \
            "不应有任何卖出信号: %s" % to_sell
        print("  [PASS] 无任何卖出信号（正确）")

    # ----------------------------------------------------------
    # 场景 4: 换手率缓存正确标记 -1
    # ----------------------------------------------------------
    def test_turnover_cache_set_to_minus_one_when_api_missing(self):
        """API 不可用时，main_loop 应设置换手率缓存为 -1"""
        C, mock_data = make_mock_C(turnover_api_available=False)
        strat = self._reload_module(C)

        # 模拟有持仓
        code = '000001.SZ'
        strat._g_my_codes = {
            code: {
                'buy_price': 10.0,
                'shares': 1000,
                'peak_price': 10.0,
            }
        }
        strat._g_turnover_cache = {}
        strat._g_all_data = mock_data
        strat._g_cooling_until = 0  # 不冷却

        # 执行主循环
        strat._main_loop(C)

        # 验证：换手率缓存被设为 -1
        assert code in strat._g_turnover_cache, \
            "持仓代码应在缓存中"
        assert strat._g_turnover_cache[code] == -1, \
            "API不可用时缓存应为 -1，实际=%s" % strat._g_turnover_cache[code]
        print("  [PASS] 换手率缓存=%d (应为 -1)" % strat._g_turnover_cache[code])

    # ----------------------------------------------------------
    # 场景 5: get_turnover_rate 异常不影响其他功能
    # ----------------------------------------------------------
    def test_other_functions_unaffected_by_missing_turnover_api(self):
        """ATR 计算和成交额排序在 API 不可用时仍正常工作"""
        C, mock_data = make_mock_C(turnover_api_available=False)
        strat = self._reload_module(C)

        strat._g_my_codes = {}
        strat._g_hold_pool_cache = None
        strat._g_hold_pool_cache_date = ''
        strat._g_turnover_cache = {}

        result = strat._run_screening(C)

        # 验证：候选按成交额降序排列
        if len(result) >= 2:
            for i in range(len(result) - 1):
                assert result[i]['amount'] >= result[i+1]['amount'], \
                    "候选应按成交额降序排列"
            print("  [PASS] 候选按成交额降序排列正确")

        # 验证：ATR% 计算正确（高波动股票被过滤）
        for r in result:
            df = mock_data[r['code']]
            atr_pct = strat._calc_atr_pct(df)
            assert atr_pct < 6.0, \
                "ATR%%=%.2f 应 < 6.0" % atr_pct
            assert r['atr_pct'] == atr_pct, \
                "atr_pct 字段应与计算一致"
        print("  [PASS] ATR%% 计算和过滤正常")

        # 验证：结果数量不超过 _MAX_HOLD
        assert len(result) <= strat._MAX_HOLD, \
            "结果不应超过 _MAX_HOLD=%d，实际=%d" % (strat._MAX_HOLD, len(result))
        print("  [PASS] 结果数量 %d ≤ _MAX_HOLD=%d" % (len(result), strat._MAX_HOLD))
