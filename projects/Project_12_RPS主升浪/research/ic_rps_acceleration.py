# coding=utf-8
"""用真实数据验证 RPS 加速度因子的 IC（决定性结论）。

从 E:/astock 读全 A 日线，计算 RPS 加速度因子 + 前向收益，测 IC/ICIR。
若 IC 全负或接近 0，则确认该因子在 A 股无预测力（与通宵研究一致）。

用法：
  python projects/Project_12_RPS主升浪/research/ic_rps_acceleration.py
"""
import sys
sys.path.insert(0, "D:/QuantLab")

import numpy as np
import pandas as pd

# ---- 数据加载（复用框架 astock_reader，避免手动处理 parquet index）----
def load_sample(n_codes=500, start="2023-01-01", end="2025-12-31"):
    """用 astock_reader 加载样本股票日线。"""
    from data.astock_reader import AstockParquetReader
    reader = AstockParquetReader("E:/astock/daily/stock_daily.parquet", adjustment="qfq")
    # 读全 A 覆盖的代码（reader 会自己处理 index）
    cov = reader.coverage(codes=None, start_date=start, end_date=end)
    all_codes = sorted(cov.keys()) if isinstance(cov, dict) else list(cov)
    sample_codes = all_codes[:n_codes]
    mw = reader.load_window(sample_codes, start, end)
    reader.close()
    # 合并成面板
    frames = []
    for code, df in mw.items():
        d = df.copy()
        d["ts_code"] = code
        d["date"] = pd.to_datetime(d["date"])
        frames.append(d)
    panel = pd.concat(frames).set_index(["date", "ts_code"])[["close"]]
    return panel


def compute_acceleration(panel, window=20, accel_window=5):
    """计算 RPS 加速度（排名差方案）。返回因子面板。"""
    close_wide = panel["close"].unstack("ts_code")
    ret_wide = close_wide / close_wide.shift(window) - 1.0
    rps_wide = ret_wide.rank(axis=1, pct=True) * 100.0
    accel_wide = rps_wide - rps_wide.shift(accel_window)
    accel_wide = accel_wide.fillna(0.0)
    result = accel_wide.stack()
    result.name = "rps_acceleration"
    return result


def compute_acceleration_ret(panel, window=20, accel_window=5):
    """计算涨幅加速度方案（方案B）：近5日涨幅 - 前5日涨幅，再截面rank。"""
    close_wide = panel["close"].unstack("ts_code")
    ret5 = close_wide / close_wide.shift(5) - 1.0
    accel = ret5 - ret5.shift(5)
    accel_rank = accel.rank(axis=1, pct=True) * 100.0
    accel_rank = accel_rank.fillna(50.0)
    result = accel_rank.stack()
    result.name = "rps_acceleration_ret"
    return result


def ic_stats(factor_panel, close_panel, forward_days=20):
    """计算因子 IC/ICIR。"""
    close_wide = close_panel["close"].unstack("ts_code")
    fwd = close_wide.pct_change(forward_days).shift(-forward_days)
    fwd = fwd.stack().rename("fwd")

    merged = pd.concat([factor_panel, fwd], axis=1).dropna()

    daily_ics = []
    for date, group in merged.groupby(level=0):
        x = group[factor_panel.name]
        y = group["fwd"]
        if len(x) < 30:
            continue
        ic = x.corr(y)
        if not np.isnan(ic):
            daily_ics.append(ic)
    if not daily_ics:
        return None
    s = pd.Series(daily_ics)
    return {
        "ic_mean": round(s.mean(), 4),
        "icir": round(s.mean() / s.std(), 4) if s.std() > 0 else 0,
        "ic_positive_pct": round((s > 0).mean(), 4),
        "n_dates": len(daily_ics),
        "ic_mean_abs": round(abs(s.mean()), 4),
    }


if __name__ == "__main__":
    print("=== RPS 加速度因子 真实数据 IC 验证 ===\n")
    print("加载样本数据（500只, 2023-2025）...")
    panel = load_sample(n_codes=500, start="2023-01-01", end="2025-12-31")
    print("样本: %d 行, %d 只股票" % (len(panel), panel.index.get_level_values("ts_code").nunique()))

    for fwd in [5, 20]:
        print("\n--- 前向 %d 日 ---" % fwd)
        f1 = compute_acceleration(panel)
        s1 = ic_stats(f1, panel, forward_days=fwd)
        print("方案A 排名差 ΔRPS: %s" % s1)
        f2 = compute_acceleration_ret(panel)
        s2 = ic_stats(f2, panel, forward_days=fwd)
        print("方案B 涨幅加速度:  %s" % s2)

    print("\n=== 结论判断 ===")
    print("若 |IC|<0.02 或 IC 为负 -> 因子在 A 股无预测力（与通宵研究一致）")
