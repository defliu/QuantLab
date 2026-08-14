# coding=utf-8
"""RPS 加速度因子 —— 源自 MCRPS 已验证有效的 ΔRPS20 设计。

⚠️ 2026-08-14 独立验证证伪：用真实 A 股（500只, 2023-2025, astock qfq）测 IC，
两种实现（排名差 / 涨幅加速度）IC 均为负（-0.0154 / -0.0097 @20日，ICIR<0.15），
说明该因子在 A 股全市场无预测力，与通宵研究"动量/趋势因子 IC 全负"一致。
MCRPS 里的 +4.62pp 是 2024-2025 特定两年 + 牛市场景的局部现象，不具备全周期稳健性。
本文件保留为方法参考（如何实现 RPS + 加速度），不作为可投入因子。

背景：MCRPS（多维度复合 RPS）策略卡 2026-07-01 验证，RPS 加速度因子
（v1→v2 收益 +33.30%→+37.92%，+4.62pp）是 MCRPS 里唯一"没被证伪"的
alpha 增量。RPS 主升浪研究（2026-08-14）证伪后，本因子作为独立可复用
因子提取保留，但独立 IC 验证同样证伪。

定义：RPS = 过去 N 日涨幅在全市场的截面百分位（0-100）。
RPS 加速度 = RPS 的短期变化（当前 RPS 相对 M 日前 RPS 的增量）。
加速度 > 0 表示"正在走强"（从低位快速上升），加速度 < 0 表示"正在走弱"。
"""

import numpy as np
import pandas as pd

from factors.base import FactorBase


class RPSAcceleration(FactorBase):
    """RPS 加速度因子

    计算：
      1. RPS_N = 过去 N 日涨幅的截面百分位（0-100）
      2. RPS 加速度 = RPS_N 相对 M 个交易日前的变化量
    方向：加速度越大越好（正在走强）。
    """

    def __init__(self, window=20, accel_window=5, description="RPS 加速度因子"):
        super().__init__(
            name="rps_acceleration",
            category="动量",
            description=description or "RPS 短期加速度（ΔRPS）",
        )
        self.window = window          # RPS 计算窗口（日）
        self.accel_window = accel_window  # 加速度回看窗口（日）

    def compute(self, panel, fin_ffill, **kwargs):
        """计算全时段 RPS 加速度因子值。

        Args:
            panel: MultiIndex(date, code) -> [close, ...]

        Returns:
            pd.Series: index=(date, code), values=因子值（截面百分位 0-100）
        """
        if "close" not in panel.columns:
            raise ValueError("panel 缺少 close 列")

        close = panel["close"]
        # 展开为宽表 (date × code)
        close_wide = close.unstack("ts_code")

        # 1) 过去 N 日涨幅
        ret_wide = close_wide / close_wide.shift(self.window) - 1.0

        # 2) RPS 截面百分位（每行跨股票排名，0-100）
        rps_wide = ret_wide.rank(axis=1, pct=True) * 100.0

        # 3) 加速度 = 当前 RPS - M 日前 RPS
        accel_wide = rps_wide - rps_wide.shift(self.accel_window)

        # 4) 处理 NaN（前 warmup 期）
        accel_wide = accel_wide.fillna(0.0)

        # 压缩回 MultiIndex
        result = accel_wide.stack()
        result.name = self.name
        return result


# ============================================================
# 单日计算版本（兼容旧接口，用于逐日调仓）
# ============================================================
def compute_single(panel, date, trade_dates, date_series, window=20, accel_window=5):
    """计算单日 RPS 加速度因子截面值。

    Args:
        panel: MultiIndex DataFrame
        date: 目标日期
        trade_dates: 交易日列表
        date_series: 目标日截面 Series (用于对齐索引)
        window: RPS 计算窗口（日）
        accel_window: 加速度回看窗口（日）

    Returns:
        pd.Series: 因子值, index=ts_code
    """
    try:
        date_idx = trade_dates.index(date)
    except ValueError:
        return pd.Series(0.0, index=date_series.index)

    need = window + accel_window + 1
    if date_idx < need:
        return pd.Series(0.0, index=date_series.index)

    start = trade_dates[date_idx - need]
    close_wide = panel.loc[start:date, "close"].unstack("ts_code")

    # 过去 N 日涨幅
    ret_wide = close_wide / close_wide.shift(window) - 1.0
    # RPS 截面百分位
    rps_wide = ret_wide.rank(axis=0, pct=True) * 100.0
    # 加速度 = 最后一行 RPS - accel_window 日前 RPS
    if len(rps_wide) >= accel_window + 1:
        accel = rps_wide.iloc[-1] - rps_wide.iloc[-1 - accel_window]
        result = accel.reindex(date_series.index).fillna(0.0)
    else:
        result = pd.Series(0.0, index=date_series.index)
    return result


# ============================================================
# 便捷函数（策略直接调用）
# ============================================================
def rps_acceleration_latest(df, window=20, accel_window=5, pool_ret=None):
    """计算单只股票当前 RPS 加速度。

    用于策略层面：给定股票日线 + 全市场涨幅截面（pool_ret: {code: ret}），
    返回该股票的 RPS 加速度百分位。

    Args:
        df: 个股日线 DataFrame（含 close 列）
        window: RPS 计算窗口
        accel_window: 加速度回看窗口
        pool_ret: 全市场 {code: N日涨幅}（用于截面排名），若为 None 则用
                  df 自身历史排名（退化，不推荐）

    Returns:
        float: RPS 加速度（0-100 百分位差异，可正可负）
    """
    if df is None or len(df) < window + accel_window + 1:
        return 0.0
    close = df["close"].astype(float)
    # 当前 N 日涨幅
    cur_ret = float(close.iloc[-1] / close.iloc[-window - 1] - 1.0)
    # M 日前 N 日涨幅
    prev_ret = float(close.iloc[-1 - accel_window] / close.iloc[-window - 1 - accel_window] - 1.0)

    if pool_ret is None:
        return cur_ret - prev_ret  # 退化：涨幅增量

    # 全市场截面百分位
    all_rets = list(pool_ret.values()) + [cur_ret, prev_ret]
    if not all_rets:
        return 0.0
    cur_pct = sum(1 for r in all_rets if r <= cur_ret) / len(all_rets) * 100.0
    prev_pct = sum(1 for r in all_rets if r <= prev_ret) / len(all_rets) * 100.0
    return cur_pct - prev_pct
