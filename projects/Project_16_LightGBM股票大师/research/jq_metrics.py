# -*- coding: utf-8 -*-
"""聚宽风格完整回测指标：三策略各自最优配置，官方引擎逐日序列 + 全套指标。

产出（可重建）：
  research/results/jq_metrics_<date>.json  全套指标 + 逐日净值/回撤/月度收益

背景（T-20260909-009 网格寻优 + T-20260910-001）：
  三策略各自最优配置（防过拟合后的候选上线配置）：
    G2   = live_trail/N15/红线60/TOP2（g2_strong_real 08-25）
    融合 = live_trail/N10/红线60/TOP2（rank w=0.5 G2×v3_enh）
    V1.4 = fixed/N10/红线58/TOP2 + lite（v3_enh）
  指标口径：复利 250 交易日年化、无风险 2%、基准 = 全市场等权（面板 fwd_ret 均值）。

用法：python research/jq_metrics.py   （在项目根目录运行，复用官方引擎）
"""
import os
import sys
import json
import importlib
import datetime

import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # research/.. = 项目根
sys.path.insert(0, PROJ)
import data_config as DC  # noqa: E402

RF_ANN = 0.02  # 无风险年化 2%（聚宽口径）

M = lambda p: os.path.join(DC.MODEL_DIR, p)  # noqa: E731
D = lambda p: os.path.join(DC.DATA_DIR, p)   # noqa: E731

CONFIGS = {
    "G2": {
        "env": {"BT_PANEL": D("feature_panel_v3_enh2_n3_bt.parquet"),
                "BT_MODEL": M("lgb_model_v3_g2_strong_real_20260825_1964t.txt"),
                "BT_META": D("features_v3_g2_strong_real_20260825.json"),
                "BT_THRESHOLD": "60.0", "BT_EXIT": "live_trail", "BT_SLIPS": "0.001"},
        "N": 15, "TOP": 2, "tag": "live_trail/N15/红线60/TOP2",
    },
    "融合": {
        "env": {"BT_PANEL": D("feature_panel_v3_enh2_n3_bt.parquet"),
                "BT_MODEL": M("lgb_model_v3_g2_strong_real_20260825_1964t.txt"),
                "BT_META": D("features_v3_g2_strong_real_20260825.json"),
                "BT_MODEL2": M("lgb_model_v3_enh.txt"),
                "BT_META2": D("features_v3_enh.json"),
                "BT_ENSEMBLE": "0.5",
                "BT_THRESHOLD": "60.0", "BT_EXIT": "live_trail", "BT_SLIPS": "0.001"},
        "N": 10, "TOP": 2, "tag": "live_trail/N10/红线60/TOP2",
    },
    "V1.4": {
        "env": {"BT_PANEL": D("feature_panel_v3_sc.parquet"),
                "BT_MODEL": M("lgb_model_v3_enh.txt"),
                "BT_META": D("features_v3_enh.json"),
                "BT_THRESHOLD": "58.0", "BT_EXIT": "fixed",
                "BT_POSFILTER": "lite", "BT_SLIPS": "0.001"},
        "N": 10, "TOP": 2, "tag": "fixed/N10/红线58/TOP2+lite",
    },
}

import scan_rotate_cost_real as SC  # noqa: E402


def run_cfg(cfg):
    # 清掉可能残留的融合/过滤变量
    for k in ("BT_MODEL2", "BT_META2", "BT_ENSEMBLE", "BT_POSFILTER", "BT_TOP", "BT_START", "BT_END"):
        os.environ.pop(k, None)
    for k, v in cfg["env"].items():
        os.environ[k] = v
    os.environ["BT_TOP"] = str(cfg["TOP"])
    importlib.reload(SC)
    dates, per_day, market_daily, open_map = SC.build_per_day()
    trades, daily_ret, n_skip = SC.simulate(dates, per_day, cfg["N"], 0.001, open_map, exec_ok=True)
    idx = pd.to_datetime(dates)
    strat = pd.Series(daily_ret.values, index=idx).fillna(0.0)
    bench = pd.Series(market_daily.values, index=idx).fillna(0.0)
    return trades, strat, bench


def metrics(trades, strat, bench):
    n = len(strat)
    nav = (1 + strat).cumprod()
    bnav = (1 + bench).cumprod()
    total = float(nav.iloc[-1] - 1)
    btotal = float(bnav.iloc[-1] - 1)
    ann = float((1 + total) ** (252 / n) - 1) if total > -1 else -1.0
    bann = float((1 + btotal) ** (252 / n) - 1) if btotal > -1 else -1.0
    excess_ann = float((strat - bench).mean() * 252)          # 算术年化超额
    rf_d = RF_ANN / 252
    vol = float(strat.std(ddof=1) * np.sqrt(252)) if n > 1 else np.nan
    sharpe = float((strat.mean() - rf_d) / strat.std(ddof=1) * np.sqrt(252)) if n > 1 and strat.std() > 0 else np.nan
    down = strat[strat < 0]
    sortino = float((strat.mean() - rf_d) / down.std(ddof=1) * np.sqrt(252)) if len(down) > 1 and down.std() > 0 else np.nan
    te = float((strat - bench).std(ddof=1) * np.sqrt(252)) if n > 1 else np.nan
    ir = float((strat - bench).mean() / (strat - bench).std(ddof=1) * np.sqrt(252)) if n > 1 and (strat - bench).std() > 0 else np.nan
    if bench.var() > 0:
        beta = float(np.cov(strat, bench)[0, 1] / np.var(bench))
        alpha = float((strat.mean() - rf_d) - beta * (bench.mean() - rf_d)) * 252
    else:
        beta, alpha = np.nan, np.nan
    mdd = float((nav / nav.cummax() - 1).min())
    bmdd = float((bnav / bnav.cummax() - 1).min())
    calmar = float(ann / abs(mdd)) if mdd < 0 else np.nan
    rets = [t[2] for t in trades]
    fwd = pd.Series(rets)
    win = float((fwd > 0).mean()) if len(fwd) else np.nan
    aw = float(fwd[fwd > 0].mean()) if (fwd > 0).any() else np.nan
    al = float(fwd[fwd < 0].mean()) if (fwd < 0).any() else np.nan
    pl = float(aw / abs(al)) if al else np.nan
    avg_hold = float(np.mean([(t[1] - t[0]) for t in trades])) if trades else np.nan
    m = pd.DataFrame({"nav": nav}).resample("ME").last()
    m_ret = (m["nav"] / m["nav"].shift(1) - 1).dropna()
    monthly = {k.strftime("%Y-%m"): float(v) for k, v in m_ret.items()}
    return {
        "n_days": n, "n_trades": len(trades),
        "total_return": total, "annual_return": ann,
        "bench_total": btotal, "bench_annual": bann,
        "excess_annual": excess_ann,
        "volatility": vol, "max_drawdown": mdd, "bench_max_drawdown": bmdd,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "alpha": alpha, "beta": beta, "info_ratio": ir, "tracking_error": te,
        "win_rate": win, "profit_loss_ratio": pl, "avg_hold_days": avg_hold,
        "monthly": monthly,
        "nav_series": [round(x, 6) for x in nav.values],
        "drawdown_series": [round(x, 6) for x in (nav / nav.cummax() - 1).values],
        "date_labels": [d.strftime("%Y-%m-%d") for d in strat.index],
        "bench_nav": [round(x, 6) for x in bnav.values],
    }


def main():
    out = {"meta": {"period": "2024-07-01 ~ 2026-08-14", "rf": RF_ANN,
                    "cost": "佣金万2双边+印花税万5卖出+过户费万0.1 | 滑点0.1%/边 | open→open可执行"},
           "strategies": {}}
    for name, cfg in CONFIGS.items():
        print(f"[{name}] 运行官方引擎 ...")
        trades, strat, bench = run_cfg(cfg)
        m = metrics(trades, strat, bench)
        m["tag"] = cfg["tag"]
        out["strategies"][name] = m
        print(f"  总收益 {m['total_return']*100:.2f}% | 年化 {m['annual_return']*100:.2f}% | "
              f"夏普 {m['sharpe']:.2f} | 回撤 {m['max_drawdown']*100:.1f}% | 交易 {m['n_trades']} | "
              f"胜率 {m['win_rate']*100:.1f}%")
    res_dir = os.path.join(PROJ, "research", "results")
    os.makedirs(res_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    out_path = os.path.join(res_dir, f"jq_metrics_{stamp}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写入", out_path)


if __name__ == "__main__":
    main()
