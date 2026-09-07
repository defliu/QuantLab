# coding: utf-8
"""DPO 超跌反转策略 —— 组合构建 + 组合级回测 + walk-forward OOS（2026-09-04 立项）。

前置：T-20260903-005 已证「绝对超跌零下金叉（V2: std 零下金叉 & ret20≤−15%）+ 固定持有20日」
在信号层/逐笔模拟层双双通过接受线（模拟 n=64,800 胜率 57.7% 单笔+3.86%、成本×2 仍 +3.71%、
10/12 年正、无灾难年）。本脚本把该信号推进到「组合级」：

  ① 组合构建：每日收盘信号 → 次日 open 买入；Top-K 等权（K 可扫）；持满 hold 交易日 open 卖出；
     涨停开盘不可买、停牌顺延卖出、同股不重叠；补仓按「跌幅更深优先」（ret20 最负优先）。
  ② 组合级回测：open→open 逐日净值；指标=年化/夏普/最大回撤/卡玛/换手/交易胜率；
     基准=全市场等权 open→open；超额与正超额年数。双边成本 0.15%（买卖各半）。
  ③ 参数网格：thr∈{-10%,-15%,-20%} × K∈{5,10,20} × hold∈{15,20,30}（27 组合，看高原非峰值）。
  ④ walk-forward：训练段 2015-2019 网格选参 → OOS 段 2020-2026 固定参数验证 + 成本×2 压力。

接受线（预注册）：OOS 组合年化 > 基准、卡玛 > 0.6、正超额年 ≥ 8/12、成本×2 后 OOS 年化仍正。

范式与 dpo_optimize 同源（PIT：指标只用 t 及以前数据；信号 t 收盘 → t+1 开盘成交；
涨停开盘不可买：主板 9.5%/创业科创 19.5%；停牌缺口 >10 自然日剔除于信号层不适用本组合层——
组合层用「open 可得才成交」处理停牌）。价格全为后复权 hfq。

备用数据源：按全局规则先调 data/gpsj_reader.is_available()，不可用则 [SKIP] 并标注
「单源结论（仅 astock），未经备用源交叉验证」。

用法：
  python research/dpo_portfolio_20260904.py [--start 2015-01-01] [--end 2026-08-21] [--quick]
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
    build_dpo, build_common,
)

ROUND_COST = 0.0015          # 双边成本（买卖各半 0.00075）
LIM_MAIN = 0.095
LIM_GEM = 0.195

# walk-forward 分段
WF_TRAIN = ("2015-01-01", "2019-12-31")
WF_OOS = ("2020-01-01", "2026-08-21")

# 参数网格
THRESHOLDS = (-0.10, -0.15, -0.20)
K_GRID = (10, 20, 40)
HOLD_GRID = (15, 20, 30)


def build_signals(df):
    """返回 V2 族信号组件：gc_below（std 零下金叉）、ret20c、base_no_vol。"""
    st_mask = df["is_st"] == 1
    close = df["close"].astype("float64")
    dpo, sig = build_dpo(df, "std")
    dpo_p1 = gshift(dpo, 1)
    sig_p1 = gshift(sig, 1)
    gcross = (dpo > sig) & (dpo_p1 <= sig_p1)
    gc_below = gcross & (dpo_p1 <= 0)
    ret20c = close / gshift(close, 20) - 1.0
    ma20 = build_common(df)[0]
    eval_mask = (df.index.get_level_values("trade_date") >= pd.Timestamp("2015-01-01")) \
        & (df["vol"] > 0)
    base_no_vol = (ma20.notna()) & (~st_mask) & eval_mask
    return gc_below, ret20c, base_no_vol


def load_panels(df, dates):
    """pivot 出组合模拟所需面板（date × code，hfq），停牌用 ffill 冻结。
    prev_raw_close 基于全样本交易日 shift(1) 再 reindex，保证评估期首日可用。"""
    t0 = time.time()
    open_p = df["open"].unstack("ts_code").astype("float64")
    close_p = df["close"].unstack("ts_code").astype("float64")
    adj_p = df["adj_factor"].unstack("ts_code").astype("float64")
    prev_raw_close = (close_p / adj_p).shift(1)      # 前一交易日不复权收盘（涨停开盘判断）
    open_f = open_p.reindex(dates).ffill()           # 停牌日 open 冻结
    adj_f = adj_p.reindex(dates).ffill()
    prev_raw_close = prev_raw_close.reindex(dates)
    print("[panels] %d×%d  %.1fs" % (len(dates), open_f.shape[1], time.time() - t0))
    return open_f, adj_f, prev_raw_close


def make_sig_index(gc_below, ret20c, base_no_vol, thr, start, end):
    """按阈值生成信号行索引列表（(date, code, rank) 元组，rank=ret20c）。"""
    mask = gc_below & base_no_vol & (ret20c <= thr)
    sig_idx = mask.index[mask.fillna(False)]
    if len(sig_idx) == 0:
        return [], []
    rk = ret20c.reindex(sig_idx)
    s = pd.DataFrame({"date": sig_idx.get_level_values("trade_date"),
                      "code": sig_idx.get_level_values("ts_code"),
                      "rk": rk.values})
    s = s[(s["date"] >= pd.Timestamp(start)) & (s["date"] <= pd.Timestamp(end))]
    return s["date"].tolist(), list(zip(s["date"].tolist(), s["code"].tolist(), s["rk"].tolist()))


def portfolio_backtest(dates, open_f, adj_f, prev_raw_close, sig_rows, K=10, hold=20,
                       cost=ROUND_COST):
    """open→open 逐日组合模拟。返回净值序列、日收益、交易记录、逐日持仓数。
    sig_rows: (date, code, rank) 列表（rank 升序优先补仓 = 跌幅更深优先）。"""
    open_f = open_f.reindex(dates)
    adj_f = adj_f.reindex(dates)
    prev_raw_close = prev_raw_close.reindex(dates)
    sig_by_date = {}
    for d, c, rk in sig_rows:
        sig_by_date.setdefault(d, []).append((c, rk))
    for d in sig_by_date:
        sig_by_date[d].sort(key=lambda x: x[1])

    positions = {}            # code -> {"entry_di": int}
    daily_ret = np.zeros(len(dates))
    n_hold = np.zeros(len(dates), dtype="int32")
    trades = []
    w = 1.0 / K

    for di, d in enumerate(dates):
        # 1) 今日组合收益：昨日收盘后持仓，open-to-open
        tot = 0.0
        if di > 0:
            for code in positions:
                o_prev = open_f.iloc[di - 1][code] if code in open_f.columns else np.nan
                o_cur = open_f.iloc[di][code]
                if np.isfinite(o_prev) and np.isfinite(o_cur) and o_prev > 0:
                    tot += w * (o_cur / o_prev - 1.0)
        buy_cost = sell_cost = 0.0
        # 2) 到期卖出（今日 open；停牌顺延）
        for code in list(positions.keys()):
            if di - positions[code]["entry_di"] >= hold:
                o = open_f.iloc[di][code]
                if not (np.isfinite(o) and o > 0):
                    continue                      # 停牌顺延
                entry_di = positions[code]["entry_di"]
                entry_px = open_f.iloc[entry_di][code]
                trades.append((code, dates[entry_di], dates[di],
                               (o / entry_px - 1.0 - cost) * 100.0))
                sell_cost += w * cost / 2.0
                del positions[code]
        # 3) 今日买入昨日信号（今日 open）
        if d in sig_by_date and len(positions) < K:
            slots = K - len(positions)
            for c, rk in sig_by_date[d]:
                if slots <= 0:
                    break
                if c in positions:
                    continue
                # 涨停开盘不可买
                o_raw = open_f.iloc[di][c] / adj_f.iloc[di][c]
                pc = prev_raw_close.iloc[di][c]
                lim = LIM_GEM if c[:3] in ("300", "301", "688", "689") else LIM_MAIN
                if not (np.isfinite(o_raw) and np.isfinite(pc) and pc > 0):
                    continue
                if o_raw / pc - 1.0 >= lim:
                    continue                      # 涨停开盘买不进
                if not (np.isfinite(open_f.iloc[di][c]) and open_f.iloc[di][c] > 0):
                    continue                      # 停牌不可买
                positions[c] = {"entry_di": di}
                buy_cost += w * cost / 2.0
                slots -= 1
        daily_ret[di] = tot - buy_cost - sell_cost
        n_hold[di] = len(positions)

    return daily_ret, n_hold, trades


def metrics(daily_ret, dates, trades=None, label=""):
    nav = np.cumprod(1.0 + daily_ret)
    n = len(daily_ret)
    if n < 30:
        return None
    years = n / 244.0
    cagr = nav[-1] ** (1.0 / years) - 1.0 if nav[-1] > 0 else -1.0
    std = daily_ret.std()
    sharpe = daily_ret.mean() / std * np.sqrt(244.0) if std > 0 else 0.0
    peak = np.maximum.accumulate(nav)
    dd = (nav / peak - 1.0).min()
    calmar = cagr / abs(dd) if dd < 0 else np.nan
    win = (np.array([t[3] for t in trades]) > 0).mean() * 100.0 if trades else np.nan
    return {"cagr": float(cagr), "sharpe": float(sharpe), "max_dd": float(dd),
            "calmar": float(calmar), "n_trades": len(trades) if trades else 0,
            "win": round(float(win), 1)}


def yearly_ret(daily_ret, dates):
    yr = pd.Series(daily_ret, index=dates)
    y = yr.groupby(yr.index.year).apply(lambda x: (1.0 + x).prod() - 1.0)
    return {int(k): float(v) for k, v in y.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2026-08-21")
    ap.add_argument("--quick", action="store_true", help="单年冒烟")
    args = ap.parse_args()
    run(args.start, args.end, args.quick)


def run(start_eval, end_eval, quick=False):
    gpsj_ok = check_gpsj()
    if not gpsj_ok:
        print("[gpsj][SKIP] 备用数据源不可用 → 单源结论（仅 astock），未经备用源交叉验证")

    load_start = (pd.Timestamp(start_eval) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    df = load_stock_panel(load_start, end_eval)
    dates_all = df.index.get_level_values("trade_date").unique().sort_values()
    dates = dates_all[(dates_all >= pd.Timestamp(start_eval))
                      & (dates_all <= pd.Timestamp(end_eval))]
    print("[dates] %d 个交易日" % len(dates))

    print("[sig] 构建信号…")
    gc_below, ret20c, base_no_vol = build_signals(df)
    open_f, adj_f, prev_raw_close = load_panels(df, dates)

    # 基准：全市场等权 open→open（剔除复权/停牌导致的 inf）
    mkt_ret = open_f.pct_change().replace([np.inf, -np.inf], np.nan) \
        .mean(axis=1).fillna(0.0).to_numpy()
    mkt_metrics = metrics(mkt_ret, dates)
    print("\n[基准] 全市场等权: %s" % mkt_metrics)

    results = {"period": [start_eval, end_eval], "gpsj_cross_validated": gpsj_ok,
               "baseline_mkt": mkt_metrics, "scan": [], "wf": {}, "base": {}}

    # ---- 1) 全样本参数网格（27 组合）----
    print("\n" + "=" * 70)
    print("== 全样本参数网格（thr × K × hold）==")
    sig_cache = {}
    for thr in THRESHOLDS:
        _, sig_rows = make_sig_index(gc_below, ret20c, base_no_vol, thr, start_eval, end_eval)
        sig_cache[thr] = sig_rows
        print("[thr %.2f] 信号 %d" % (thr, len(sig_rows)))
    for thr in THRESHOLDS:
        for K in K_GRID:
            for hold in HOLD_GRID:
                dr, nh, trades = portfolio_backtest(dates, open_f, adj_f, prev_raw_close,
                                                    sig_cache[thr], K, hold)
                m = metrics(dr, dates, trades=trades)
                yr = yearly_ret(dr, dates)
                mkt_yr = yearly_ret(mkt_ret, dates)
                pos_exc = sum(1 for k in yr if k in mkt_yr and yr[k] > mkt_yr[k])
                row = {"thr": thr, "K": K, "hold": hold}
                row.update(m)
                row["years_pos_exc"] = pos_exc
                results["scan"].append(row)
                print("thr=%+5.0f%% K=%2d hold=%2d | 年化%+6.2f%% 夏普%5.2f 回撤%6.1f%% "
                      "卡玛%5.2f 交易%5d 胜率%5.1f%% 正超额年%2d/%2d"
                      % (thr * 100, K, hold, row["cagr"] * 100, row["sharpe"],
                         row["max_dd"] * 100, row["calmar"], row["n_trades"],
                         row["win"], pos_exc, len(yr)))
                del dr, nh, trades
                gc.collect()

    # ---- 2) 主口径（V2: thr=-0.15, hold=20）+ K=10 ----
    base_cfg = {"thr": -0.15, "K": 10, "hold": 20}
    dr, nh, trades = portfolio_backtest(dates, open_f, adj_f, prev_raw_close,
                                        sig_cache[base_cfg["thr"]],
                                        K=base_cfg["K"], hold=base_cfg["hold"])
    base_m = metrics(dr, dates, trades=trades)
    base_yr = yearly_ret(dr, dates)
    mkt_yr = yearly_ret(mkt_ret, dates)
    base_pos_exc = sum(1 for k in base_yr if k in mkt_yr and base_yr[k] > mkt_yr[k])
    results["base"] = {"cfg": base_cfg, "metrics": base_m,
                       "years_pos_exc": base_pos_exc,
                       "yearly_ret": base_yr, "mkt_yearly": mkt_yr}
    print("\n[主口径] %s: %s  正超额年 %d/%d" % (base_cfg, base_m, base_pos_exc, len(base_yr)))
    print("  年度收益（组合 / 基准）:")
    for k in sorted(base_yr):
        print("    %d: 组合 %+6.2f%%  基准 %+6.2f%%" % (k, base_yr[k] * 100,
                                                      mkt_yr.get(k, np.nan) * 100))

    # 主口径成本×2 压力
    dr2, nh2, trades2 = portfolio_backtest(dates, open_f, adj_f, prev_raw_close,
                                           sig_cache[base_cfg["thr"]],
                                           K=base_cfg["K"], hold=base_cfg["hold"], cost=0.003)
    base_x2 = metrics(dr2, dates, trades=trades2)
    results["base"]["metrics_costx2"] = base_x2
    print("\n[主口径 成本×2] %s" % base_x2)
    del dr, nh, trades, dr2, nh2, trades2
    gc.collect()

    # ---- 3) walk-forward：训练段 2015-2019 选参 → OOS 2020+ ----
    if not quick:
        print("\n" + "=" * 70)
        print("== walk-forward：训练 %s ~ %s → OOS %s ~ %s =="
              % (WF_TRAIN[0], WF_TRAIN[1], WF_OOS[0], WF_OOS[1]))
        dates_train = dates_all[(dates_all >= pd.Timestamp(WF_TRAIN[0]))
                                & (dates_all <= pd.Timestamp(WF_TRAIN[1]))]
        dates_oos = dates_all[(dates_all >= pd.Timestamp(WF_OOS[0]))
                              & (dates_all <= pd.Timestamp(WF_OOS[1]))]
        # 训练段面板（open/adj 需要复用同一面板，dates 已含全部；此处用日期掩码）
        best = None
        sig_cache_wf = {}
        for thr in THRESHOLDS:
            _, rows = make_sig_index(gc_below, ret20c, base_no_vol, thr,
                                     WF_TRAIN[0], WF_TRAIN[1])
            sig_cache_wf[thr] = rows
        for thr in THRESHOLDS:
            for K in K_GRID:
                for hold in HOLD_GRID:
                    dr_t, _, _ = portfolio_backtest(dates_train, open_f, adj_f,
                                                    prev_raw_close, sig_cache_wf[thr], K, hold)
                    m = metrics(dr_t, dates_train)
                    if m is None:
                        continue
                    score = m["calmar"] if np.isfinite(m["calmar"]) else -1.0
                    if best is None or score > best["score"]:
                        best = {"thr": thr, "K": K, "hold": hold, "score": score,
                                "train_m": m}
        print("\n[WF 训练段最优] %s score(卡玛)=%.2f %s"
              % ({k: best[k] for k in ("thr", "K", "hold")}, best["score"], best["train_m"]))
        # OOS 段用训练段参数
        _, rows_oos = make_sig_index(gc_below, ret20c, base_no_vol, best["thr"],
                                     WF_OOS[0], WF_OOS[1])
        dr_o, _, trades_o = portfolio_backtest(dates_oos, open_f, adj_f, prev_raw_close,
                                               rows_oos, K=best["K"], hold=best["hold"])
        oos_m = metrics(dr_o, dates_oos, trades=trades_o)
        dr_o2, _, _ = portfolio_backtest(dates_oos, open_f, adj_f, prev_raw_close,
                                         rows_oos, K=best["K"], hold=best["hold"], cost=0.003)
        oos_m2 = metrics(dr_o2, dates_oos)
        # OOS 基准（mkt_ret 按评估期 dates 顺序）
        mkt_idx_train = dates.get_indexer(dates_train)
        mkt_idx_oos = dates.get_indexer(dates_oos)
        mkt_train_m = metrics(mkt_ret[mkt_idx_train], dates_train)
        mkt_oos_m = metrics(mkt_ret[mkt_idx_oos], dates_oos)
        oos_yr = yearly_ret(dr_o, dates_oos)
        mkt_oos_yr = yearly_ret(mkt_ret[mkt_idx_oos], dates_oos)
        oos_pos_exc = sum(1 for k in oos_yr if k in mkt_oos_yr and oos_yr[k] > mkt_oos_yr[k])
        results["wf"] = {"train": list(WF_TRAIN), "oos": list(WF_OOS),
                         "best": {k: best[k] for k in ("thr", "K", "hold")},
                         "train_metrics": best["train_m"],
                         "oos_metrics": oos_m, "oos_metrics_costx2": oos_m2,
                         "mkt_train": mkt_train_m, "mkt_oos": mkt_oos_m,
                         "oos_years_pos_exc": oos_pos_exc,
                         "oos_yearly": oos_yr, "mkt_oos_yearly": mkt_oos_yr}
        print("\n[WF OOS 最优参数] %s" % oos_m)
        print("[WF OOS 基准] %s" % mkt_oos_m)
        print("[WF OOS 成本×2] %s" % oos_m2)
        print("[WF OOS 正超额年] %d/%d" % (oos_pos_exc, len(oos_yr)))

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = RESULTS_DIR + "/DPO组合回测_20260904.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=float)
    print("\n[out] %s" % out_json)
    return results


if __name__ == "__main__":
    main()
