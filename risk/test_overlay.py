# coding: utf-8
"""risk overlay 单元 sanity 测试（不依赖回测大数据，纯逻辑校验）。

运行：
    cd D:/QuantLab
    python risk/test_overlay.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from risk.crash_guard import CrashGuard
from risk.regime_gate import RegimeGate
from risk.daily_overlay import build_daily_risk_overlay, DailyRiskOverlay


class FakePf:
    """最小 pf 桩，仅供 target_weights_to_decision 空仓路径跑通。"""
    def __init__(self):
        self.positions = {}

    def total_asset(self):
        return 1_000_000.0

    def position_list(self):
        return []


def test_crash_guard():
    g = CrashGuard(crash_lookback=5, crash_threshold=-0.07,
                   crash_cooldown=5, enabled=True)
    # 正常 10 天
    for _ in range(10):
        assert g.update(0.001) is False
    # 注入 5 日累计 -10% 崩盘
    around = [g.update(r) for r in [-0.02, -0.02, -0.02, -0.02, -0.02]]
    assert any(around), "崩盘应触发空仓"
    # 之后恢复，冷却应持续若干日再解除
    recover = [g.update(0.001) for _ in range(10)]
    assert recover[0] is True, "冷却首日仍应空仓"
    assert recover[-1] is False, "冷却到期应解除"
    print("[OK] CrashGuard 触发+冷却逻辑")


def test_crash_guard_disabled():
    g = CrashGuard(enabled=False)
    for _ in range(20):
        assert g.update(-0.5) is False
    print("[OK] CrashGuard 关闭时零影响")


def test_regime_gate():
    gate = RegimeGate(lookback=20, vol_percentile_threshold=0.5, enabled=True)
    for _ in range(20):  # warmup 低波动
        gate.update(0.001)
    hv = [gate.update(0.03 if i % 2 == 0 else -0.03) for i in range(20)]
    assert any(hv), "高波动 regime 应转现"
    # 数据不足 fail-open
    g2 = RegimeGate(enabled=True)
    assert g2.update(0.05) is False
    print("[OK] RegimeGate 高波动转现 + 数据不足 fail-open")


def test_overlay_disabled_passthrough():
    ov = build_daily_risk_overlay({})  # 默认双关
    assert ov.enabled is False
    dec = {"target_weights": {"000001.SZ": 1.0}}
    assert ov.apply(dec, "2020-01-01", FakePf(), {}, {}, None) is dec
    print("[OK] overlay 关闭时原样透传")


def test_overlay_forced_cash():
    ov = build_daily_risk_overlay({"crash_guard": 1, "crash_threshold": -0.05,
                                   "crash_cooldown": 3})
    assert ov.enabled is True
    dec = {"target_weights": {"000001.SZ": 1.0}, "logs": [], "diagnostics": {}}
    # 先喂 >= crash_lookback 天正常收益（不触发）
    for k in range(6):
        ov.apply(dec, "2020-01-%02d" % (k + 1), FakePf(), _win(0.001), {}, None)
    # 再喂单日 -6% 崩盘（最近 5 日累计 -5.96% <= -5% -> 触发 full-exit）
    out = ov.apply(dec, "2020-01-10", FakePf(), _win(-0.06), {}, None)
    assert "risk_overlay_exit" in out.get("diagnostics", {}), "崩盘应写入 full-exit 诊断"
    print("[OK] overlay 崩盘触发 full-exit decision")


def _win(ret):
    """造一个含单标的、今日收益=ret 的 window 桩。"""
    import pandas as pd
    prev, cur = 10.0, 10.0 * (1.0 + ret)
    df = pd.DataFrame({"close": [prev, cur]})
    return {"000001.SZ": df}


if __name__ == "__main__":
    test_crash_guard()
    test_crash_guard_disabled()
    test_regime_gate()
    test_overlay_disabled_passthrough()
    test_overlay_forced_cash()
    print("\nALL SANITY CHECKS PASSED")
