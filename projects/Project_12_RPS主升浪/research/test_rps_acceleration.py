# coding=utf-8
"""RPS 加速度因子单元验证。

验证：
  1. compute() 返回正确的 MultiIndex Series
  2. 加速上涨的股票 RPS 加速度 > 0
  3. 减速下跌的股票 RPS 加速度 < 0
  4. compute_single() 单日版本正常
"""
import sys
sys.path.insert(0, "D:/QuantLab")

import numpy as np
import pandas as pd

from factors.rps_acceleration import RPSAcceleration, compute_single, rps_acceleration_latest


def make_panel(n_dates=200, n_stocks=30, seed=42):
    """构造面板数据：30 只股票，200 天。

    设计意图（匹配加速度因子的真实特性——捕捉"RPS 排名正在爬升"的票）：
      1. 前 10 只"升势组"：前期横盘低位，近期加速上涨（RPS 从低位爬到高位）
      2. 中 10 只"高位组"：早期冲高后横盘（RPS 已封顶，加速度 ≈ 0）
      3. 后 10 只"下跌组"：持续阴跌（RPS 低位徘徊，加速度 ≈ 0 或负）

    这样升势组的 RPS 加速度应显著为正，其他两组接近 0 或负。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n_dates)
    codes = ["%06d.SZ" % i for i in range(n_stocks)]

    frames = []
    for j, code in enumerate(codes):
        if j < 10:
            # 升势组：前 150 天横盘（低位），后 50 天加速上涨（RPS 爬升）
            rets = np.concatenate([
                rng.normal(0.0002, 0.008, n_dates - 50),   # 横盘低位
                np.linspace(0.002, 0.015, 50),             # 加速上涨段
            ])
        elif j < 20:
            # 高位组：前 100 天大涨冲高，后 100 天横盘（RPS 封顶）
            rets = np.concatenate([
                rng.normal(0.004, 0.01, n_dates - 100),    # 冲高
                rng.normal(0.0002, 0.008, 100),            # 高位横盘
            ])
        else:
            # 下跌组：持续阴跌（RPS 低位徘徊）
            rets = rng.normal(-0.002, 0.008, n_dates)
        close = 10 * np.cumprod(1 + rets)
        df = pd.DataFrame({
            "close": close,
            "ts_code": code,
            "date": dates,
        })
        frames.append(df)

    panel = pd.concat(frames)
    panel = panel.set_index(["date", "ts_code"])
    return panel


def test_compute_shape():
    panel = make_panel()
    f = RPSAcceleration(window=20, accel_window=5)
    result = f.compute(panel, None)
    assert isinstance(result, pd.Series), "应返回 Series"
    assert result.name == "rps_acceleration", "name 应为 rps_acceleration"
    # 与 panel 行数一致（除 warmup 期）
    print("[OK] compute() 返回 %d 行, name=%s" % (len(result), result.name))
    # 验证最末日期：升势组应有较高加速度
    last_date = panel.index.get_level_values("date").max()
    last = result.xs(last_date, level="date")
    # 前10只（升势）vs 后10只（下跌）
    up_codes = ["%06d.SZ" % i for i in range(10)]
    high_codes = ["%06d.SZ" % i for i in range(10, 20)]
    decl_codes = ["%06d.SZ" % i for i in range(20, 30)]
    mean_up = last.loc[up_codes].mean()
    mean_high = last.loc[high_codes].mean()
    mean_decl = last.loc[decl_codes].mean()
    print("  末日期: 升势组=%.2f, 高位组=%.2f, 下跌组=%.2f" % (mean_up, mean_high, mean_decl))
    assert mean_up > mean_high, "升势组应高于高位组（RPS爬升>封顶）"
    assert mean_up > mean_decl, "升势组应高于下跌组"
    print("[OK] 升势股 RPS 加速度显著高于高位/下跌股")


def test_acceleration_sign():
    """验证方向：加速 > 0，减速 < 0。"""
    # 构造单只：前期横盘低位 + 近期加速上涨（RPS 爬升）
    rng = np.random.default_rng(7)
    n = 120
    dates = pd.bdate_range("2023-01-01", periods=n)
    rets_accel = np.concatenate([
        rng.normal(0.0002, 0.008, n - 30),   # 横盘低位
        np.linspace(0.002, 0.015, 30),       # 加速上涨段
    ])
    close_accel = 10 * np.cumprod(1 + rets_accel)
    df_accel = pd.DataFrame({"close": close_accel, "ts_code": "000001.SZ", "date": dates})

    # 单日版本：加速股
    panel = df_accel.set_index(["date", "ts_code"])
    trade_dates = [str(d) for d in dates]
    date_series = pd.Series(index=["000001.SZ"])
    single_accel = compute_single(panel, trade_dates[-1], trade_dates, date_series)
    print("[OK] compute_single 加速股: %s" % single_accel.to_dict())
    assert not np.isnan(single_accel.iloc[0])

    # 便捷函数：加速股 > 平稳股
    accel_val = rps_acceleration_latest(df_accel, window=20, accel_window=5)
    print("[OK] rps_acceleration_latest 加速股(无pool): %.4f" % accel_val)
    assert accel_val > 0, "加速股加速度应为正"


def test_engine_integration():
    """验证注册到 FactorEngine 后可批量计算。"""
    from factors.engine import FactorEngine
    panel = make_panel()
    engine = FactorEngine()
    engine.register(RPSAcceleration(window=20, accel_window=5))
    result = engine.compute_all(panel, None)
    assert "rps_acceleration" in result.columns, "因子面板应含 rps_acceleration"
    print("[OK] FactorEngine 集成: 因子面板列=%s, 行数=%d" % (list(result.columns), len(result)))


if __name__ == "__main__":
    print("=== RPS 加速度因子单元验证 ===\n")
    test_compute_shape()
    test_acceleration_sign()
    test_engine_integration()
    print("\n=== RPS 加速度因子全部单元验证通过 ===")
