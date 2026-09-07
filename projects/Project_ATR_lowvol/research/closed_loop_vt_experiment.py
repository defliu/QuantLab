# coding: utf-8
"""闭环波动率控制 离线 overlay 实验（套在 ATR_FIX_v4 净值曲线上）。

对应报告「可落地量化策略挖掘 2026-08-28」第 1 条 + 看板 T-20260829-002。
不改底层信号，仅对 ATR 策略的日收益序列做敞口缩放，验证闭环相比开环/无VT
是否同步改善夏普与回撤（前提是底层夏普>0.5，ATR 基线 0.894 满足）。

用法：python closed_loop_vt_experiment.py [equity_curve.csv]
默认读取最新 atr_10w_price50 基线产物。
"""
import sys, os, math, json, datetime
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
QUANTLAB = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, QUANTLAB)

from strategy.vol_target_sizer import ClosedLoopVolTarget, overlay_equity

DEFAULT_CURVE = os.path.join(
    QUANTLAB,
    "reports/20260816_113157_85e0a7_atr_lowvol_10w_price50/equity_curve.csv")


def _metrics(equity, weights, trading_days):
    """从缩放权益曲线计算绩效（与 analyzer.compute_metrics 同口径近似）。"""
    n = len(equity)
    total_return = equity[-1] - 1.0
    if n > 1 and equity[-1] > 0:
        cagr = equity[-1] ** (252.0 / trading_days) - 1.0 if trading_days > 0 else 0.0
    else:
        cagr = 0.0
    # 日收益
    rets = [(equity[i] / equity[i - 1] - 1.0) for i in range(1, n)]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((x - mean) ** 2 for x in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    std = math.sqrt(var) if var > 0 else 0.0
    sharpe = mean / std * math.sqrt(252.0) if std > 0 else 0.0
    # 最大回撤
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak
            if dd < mdd:
                mdd = dd
    calmar = (cagr / abs(mdd)) if mdd < 0 else None
    # overlay 换手（年化双边 = |Δw| 求和 / 年）
    dw = sum(abs(weights[i] - weights[i - 1]) for i in range(1, len(weights)))
    turnover_ann = dw / trading_days * 252.0 if trading_days > 0 else 0.0
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "overlay_turnover_ann": turnover_ann,
        "avg_weight": sum(weights) / len(weights),
    }


def main():
    curve_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CURVE
    df = pd.read_csv(curve_path)
    rets = df["daily_return"].astype(float).tolist()
    dates = df["date"].astype(str).tolist()
    trading_days = len(rets) - 1  # 首个为建仓前 0
    print("[exp] 载入 %s  交易日=%d  %s~%s" % (os.path.basename(curve_path),
          trading_days, dates[0], dates[-1]))

    # 基线（无VT）：权益曲线即原始 total_asset / 初始
    init_asset = float(df["total_asset"].iloc[0])
    base_equity = [float(v) / init_asset for v in df["total_asset"].tolist()]
    base = _metrics(base_equity, [1.0] * len(base_equity), trading_days)

    sigma_target = 0.15 / math.sqrt(252.0)   # 15% 年化目标波动（A股建议区间）
    combos = [
        ("闭环 L=1.0",  dict(L=1.0,  sigma_target_daily=sigma_target, h=126), False),
        ("闭环 L=1.5",  dict(L=1.5,  sigma_target_daily=sigma_target, h=126), False),
        ("开环 L=1.0",  dict(L=1.0,  sigma_target_daily=sigma_target, h=126), True),
        ("开环 L=1.5",  dict(L=1.5,  sigma_target_daily=sigma_target, h=126), True),
        ("闭环 L=1.5 σ=12%", dict(L=1.5, sigma_target_daily=0.12/math.sqrt(252.0), h=126), False),
    ]

    rows = []
    for name, kw, openloop in combos:
        ctrl = ClosedLoopVolTarget(**kw)
        eq, w = overlay_equity(rets, ctrl, openloop=openloop)
        m = _metrics(eq, w, trading_days)
        rows.append((name, m))

    # 打印对比表
    hdr = "%-16s %10s %9s %10s %8s %8s %10s %8s" % (
        "方案", "总收益", "CAGR", "回撤", "夏普", "卡玛", "overlay换手", "均敞口")
    print("\n" + hdr)
    print("-" * len(hdr))
    print("%-16s %10.2f%% %9.2f%% %10.2f%% %8.3f %8.3f %10.2f%% %8.2f" % (
        "基线(无VT)", base["total_return"] * 100, base["cagr"] * 100,
        base["max_drawdown"] * 100, base["sharpe"], base["calmar"] or 0.0,
        0.0, 1.0))
    for name, m in rows:
        print("%-16s %10.2f%% %9.2f%% %10.2f%% %8.3f %8.3f %10.2f%% %8.2f" % (
            name, m["total_return"] * 100, m["cagr"] * 100,
            m["max_drawdown"] * 100, m["sharpe"], m["calmar"] or 0.0,
            m["overlay_turnover_ann"] * 100, m["avg_weight"]))

    # 落盘
    out_dir = os.path.join(HERE, "..", "results")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = {
        "baseline": base,
        "combos": {name: m for name, m in rows},
        "sigma_target_annual": 0.15,
        "note": "离线 overlay，未计入 overlay 换手产生的额外交易成本",
    }
    outp = os.path.join(out_dir, "closed_loop_vt_experiment_%s.json" % stamp)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n[exp] 结果已写 %s" % outp)


if __name__ == "__main__":
    main()
