# coding: utf-8
"""暗号6「双针探底+中阳突破」—— 组合级二次验证（2026-09-04，T-20260904-012）。

前置：T-20260904-011 信号级评估发现暗号6 为本仓同族首次通过信号级多重检验的信号
（fwd20 63.0%/+5.82%、样本外两段一致 64.9%/62.6%、优于纯动量对照 +9.4pp、
模拟 hold20 +4.85%、成本×2 仍正、10/12 年正），但 n 仅 1,348 未达实盘结论。
本脚本按 DPO 组合化同规格做二次验证：

  ① 组合级回测：open→open 逐日净值（复用 dpo_portfolio_20260904.portfolio_backtest）；
     信号 t 收盘 → t+1 open 买入、持满 hold 交易日 open 卖出、涨停开盘不可买、停牌顺延；
     「全买」等权、目标持仓上限 K=10（信号稀少，实际持仓由信号决定）；双边成本 0.15%；
     基准 = 全市场等权 open→open。
  ② 参数高原（全样本 27 网格）：下影倍率{1.5,2,3} × 中阳阈值{1%,2%,3%} × hold{10,20,30}。
  ③ walk-forward：训练段 2015-2019 按卡玛选参 → OOS 2020+ 固定参数 + 成本×2 压力。
  ④ 实盘性检验：报告平均/中位/最大同时持仓（信号稀少 = 实盘只能小持仓）。

判定线（预注册）：OOS 组合年化 > 基准、OOS 卡玛 > 0.6、OOS 正超额年 ≥5/7、
成本×2 后 OOS 年化仍正。

备用数据源：按全局规则先调 data/gpsj_reader.is_available()，不可用则 [SKIP] 并标注
「单源结论（仅 astock），未经备用源交叉验证」。

用法：
  python research/yaogu_portfolio_20260904.py [--start 2015-01-01] [--end 2026-08-21] [--quick]
"""
import argparse
import gc
import json
import os
import time

import numpy as np
import pandas as pd

from dpo_feasibility_20260903 import (
    RESULTS_DIR,
    check_gpsj, load_stock_panel, gshift,
)
from dpo_portfolio_20260904 import portfolio_backtest, metrics, yearly_ret, load_panels

ROUND_COST = 0.0015
K_TARGET = 10
WF_TRAIN = ("2015-01-01", "2019-12-31")
WF_OOS = ("2020-01-01", "2026-08-21")
WICK_MULTS = (1.5, 2.0, 3.0)
YANG_PCTS = (0.01, 0.02, 0.03)
HOLD_GRID = (10, 20, 30)
BASE = {"wick_mult": 2.0, "yang_pct": 0.02, "gap": 3, "win": 5, "hold": 20}


def build_double_needle(df, wick_mult, yang_pct, gap=3, win=5):
    """暗号6 双针探底 + 中阳突破（参数化，PIT 安全）。"""
    open_ = df["open"].astype("float64")
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")
    vol = df["vol"].astype("float64")
    st_mask = df["is_st"] == 1
    vol_ok = vol > 0
    g = close.groupby(level="ts_code")

    hhv60 = gshift(g.transform(lambda x: x.rolling(60, min_periods=60).max()), 1)
    low_pos = (close <= 0.8 * hhv60) & hhv60.notna()

    body = (close - open_).abs()
    wick_low = open_.where(open_ < close, close) - low
    wick = (body > 0) & (wick_low >= wick_mult * body)

    wick_recent = wick
    for k in range(1, gap + 1):
        wick_recent = wick_recent | gshift(wick, k)
    low_gap_prev = gshift(g.transform(lambda x: x.rolling(gap, min_periods=gap).min()), 1)
    vol_gap_prev = gshift(vol.groupby(level="ts_code").transform(
        lambda x: x.rolling(gap, min_periods=gap).mean()), 1)
    hhv_win_prev = gshift(g.transform(lambda x: x.rolling(win, min_periods=win).max()), 1)
    prev_close = gshift(close, 1)
    yang_break = (close > open_) & (close / prev_close - 1.0 >= yang_pct) \
        & (close > hhv_win_prev)

    s6 = wick & wick_recent & (low > low_gap_prev) & (vol < vol_gap_prev) \
        & yang_break & low_pos & vol_ok & (~st_mask)
    return s6


def make_sig_rows(sig, start, end):
    """把信号 Series 转为 (date, code, rank) 列表（rank 恒 0：无排序键，按信号顺序补仓）。"""
    idx = sig.index[sig.fillna(False)]
    if len(idx) == 0:
        return []
    s = pd.DataFrame({"date": idx.get_level_values("trade_date"),
                      "code": idx.get_level_values("ts_code")})
    s = s[(s["date"] >= pd.Timestamp(start)) & (s["date"] <= pd.Timestamp(end))]
    return list(zip(s["date"].tolist(), s["code"].tolist(), [0.0] * len(s)))


def run(start_eval, end_eval, quick=False):
    gpsj_ok = check_gpsj()
    if not gpsj_ok:
        print("[gpsj][SKIP] 备用数据源不可用 → 单源结论（仅 astock），未经备用源交叉验证")

    load_start = (pd.Timestamp(start_eval) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    df = load_stock_panel(load_start, end_eval)
    dates_all = df.index.get_level_values("trade_date").unique().sort_values()
    dates = dates_all[(dates_all >= pd.Timestamp(start_eval))
                      & (dates_all <= pd.Timestamp(end_eval))]
    print("[dates] %d 个交易日" % len(dates))

    open_f, adj_f, prev_raw_close = load_panels(df, dates)
    mkt_ret = open_f.pct_change().replace([np.inf, -np.inf], np.nan) \
        .mean(axis=1).fillna(0.0).to_numpy()
    mkt_m = metrics(mkt_ret, dates)
    print("\n[基准] 全市场等权: %s" % mkt_m)

    results = {"period": [start_eval, end_eval], "gpsj_cross_validated": gpsj_ok,
               "baseline_mkt": mkt_m, "base": {}, "scan": [], "wf": {}}

    # ---- 1) 全样本主口径（原文参数）----
    print("\n" + "=" * 70)
    print("== 全样本主口径（原文参数 %s）==" % BASE)
    sig = build_double_needle(df, BASE["wick_mult"], BASE["yang_pct"])
    rows = make_sig_rows(sig, start_eval, end_eval)
    print("[sig] 暗号6 全样本信号 %d 个" % len(rows))
    dr, nh, trades = portfolio_backtest(dates, open_f, adj_f, prev_raw_close,
                                        rows, K=K_TARGET, hold=BASE["hold"])
    base_m = metrics(dr, dates, trades=trades)
    base_yr = yearly_ret(dr, dates)
    mkt_yr = yearly_ret(mkt_ret, dates)
    pos_exc = sum(1 for k in base_yr if k in mkt_yr and base_yr[k] > mkt_yr[k])
    hold_stats = {"mean": round(float(nh[nh > 0].mean()), 2),
                  "median": round(float(np.median(nh[nh > 0])), 1),
                  "max": int(nh.max()),
                  "days_empty": int((nh == 0).sum())}
    results["base"] = {"cfg": BASE, "metrics": base_m, "years_pos_exc": pos_exc,
                       "yearly_ret": base_yr, "mkt_yearly": mkt_yr,
                       "hold_stats": hold_stats}
    print("[主口径] %s" % base_m)
    print("[持仓] %s" % hold_stats)
    print("  年度（组合/基准）:")
    for k in sorted(base_yr, key=int):
        print("    %d: %+7.2f%% / %+7.2f%%" % (int(k), base_yr[k] * 100,
                                               mkt_yr.get(k, np.nan) * 100))
    del sig, dr, nh, trades
    gc.collect()

    # ---- 2) 全样本参数高原（27 网格）----
    if not quick:
        print("\n" + "=" * 70)
        print("== 全样本参数网格（wick_mult × yang_pct × hold）==")
        for wm in WICK_MULTS:
            for yp in YANG_PCTS:
                sig_w = build_double_needle(df, wm, yp)
                rows_w = make_sig_rows(sig_w, start_eval, end_eval)
                for hold in HOLD_GRID:
                    dr2, _, _ = portfolio_backtest(dates, open_f, adj_f, prev_raw_close,
                                                   rows_w, K=K_TARGET, hold=hold)
                    m = metrics(dr2, dates)
                    yr2 = yearly_ret(dr2, dates)
                    pos2 = sum(1 for k in yr2 if k in mkt_yr and yr2[k] > mkt_yr[k])
                    row = {"wick_mult": wm, "yang_pct": yp, "hold": hold}
                    row.update(m)
                    row["years_pos_exc"] = pos2
                    results["scan"].append(row)
                    print("wm=%.1f yp=%4.1f%% hold=%2d | 年化%+7.2f%% 回撤%6.1f%% "
                          "卡玛%6.2f 交易%5d 胜率%5.1f%% 正超额年%2d/%d"
                          % (wm, yp * 100, hold, row["cagr"] * 100, row["max_dd"] * 100,
                             row["calmar"], row["n_trades"], row["win"], pos2, len(yr2)))
                del sig_w, rows_w
                gc.collect()

        # ---- 3) walk-forward：训练 2015-2019 选参 → OOS 2020+ ----
        print("\n" + "=" * 70)
        print("== walk-forward：训练 %s~%s → OOS %s~%s =="
              % (WF_TRAIN[0], WF_TRAIN[1], WF_OOS[0], WF_OOS[1]))
        dates_train = dates_all[(dates_all >= pd.Timestamp(WF_TRAIN[0]))
                                & (dates_all <= pd.Timestamp(WF_TRAIN[1]))]
        dates_oos = dates_all[(dates_all >= pd.Timestamp(WF_OOS[0]))
                              & (dates_all <= pd.Timestamp(WF_OOS[1]))]
        best = None
        for wm in WICK_MULTS:
            for yp in YANG_PCTS:
                sig_t = build_double_needle(df, wm, yp)
                rows_t = make_sig_rows(sig_t, WF_TRAIN[0], WF_TRAIN[1])
                for hold in HOLD_GRID:
                    dr_t, _, _ = portfolio_backtest(dates_train, open_f, adj_f,
                                                    prev_raw_close, rows_t,
                                                    K=K_TARGET, hold=hold)
                    m = metrics(dr_t, dates_train)
                    if m is None:
                        continue
                    score = m["calmar"] if np.isfinite(m["calmar"]) else -1.0
                    if best is None or score > best["score"]:
                        best = {"wick_mult": wm, "yang_pct": yp, "hold": hold,
                                "score": score, "train_m": m}
                del sig_t, rows_t
                gc.collect()
        print("\n[WF 训练段最优] %s score=%.2f %s"
              % ({k: best[k] for k in ("wick_mult", "yang_pct", "hold")},
                 best["score"], best["train_m"]))
        # OOS
        sig_o = build_double_needle(df, best["wick_mult"], best["yang_pct"])
        rows_o = make_sig_rows(sig_o, WF_OOS[0], WF_OOS[1])
        dr_o, nh_o, trades_o = portfolio_backtest(dates_oos, open_f, adj_f,
                                                  prev_raw_close, rows_o,
                                                  K=K_TARGET, hold=best["hold"])
        oos_m = metrics(dr_o, dates_oos, trades=trades_o)
        dr_o2, _, _ = portfolio_backtest(dates_oos, open_f, adj_f, prev_raw_close,
                                         rows_o, K=K_TARGET, hold=best["hold"], cost=0.003)
        oos_m2 = metrics(dr_o2, dates_oos)
        mkt_idx_o = dates.get_indexer(dates_oos)
        mkt_oos_m = metrics(mkt_ret[mkt_idx_o], dates_oos)
        mkt_oos_yr = yearly_ret(mkt_ret[mkt_idx_o], dates_oos)
        oos_yr = yearly_ret(dr_o, dates_oos)
        oos_pos = sum(1 for k in oos_yr if k in mkt_oos_yr and oos_yr[k] > mkt_oos_yr[k])
        oos_hold = {"mean": round(float(nh_o[nh_o > 0].mean()), 2),
                    "median": round(float(np.median(nh_o[nh_o > 0])), 1),
                    "max": int(nh_o.max()), "days_empty": int((nh_o == 0).sum())}
        results["wf"] = {"train": list(WF_TRAIN), "oos": list(WF_OOS),
                         "best": {k: best[k] for k in ("wick_mult", "yang_pct", "hold")},
                         "train_metrics": best["train_m"],
                         "oos_metrics": oos_m, "oos_metrics_costx2": oos_m2,
                         "mkt_oos": mkt_oos_m, "oos_years_pos_exc": oos_pos,
                         "oos_yearly": oos_yr, "mkt_oos_yearly": mkt_oos_yr,
                         "oos_hold_stats": oos_hold}
        print("\n[WF OOS] %s" % oos_m)
        print("[WF OOS 基准] %s" % mkt_oos_m)
        print("[WF OOS 成本×2] %s" % oos_m2)
        print("[WF OOS 正超额年] %d/%d  持仓 %s" % (oos_pos, len(oos_yr), oos_hold))
        print("  OOS 年度（组合/基准）:")
        for k in sorted(oos_yr, key=int):
            print("    %d: %+7.2f%% / %+7.2f%%" % (int(k), oos_yr[k] * 100,
                                                   mkt_oos_yr.get(k, np.nan) * 100))
        del sig_o, rows_o, dr_o, nh_o, trades_o, dr_o2
        gc.collect()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = RESULTS_DIR + "/暗号6双针探底_组合验证_20260904.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=float)
    print("\n[out] %s" % out_json)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--quick", action="store_true", help="仅主口径冒烟")
    args = ap.parse_args()
    run(args.start, args.end, args.quick)


if __name__ == "__main__":
    main()
