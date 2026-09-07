# -*- coding: utf-8 -*-
"""独立验证 exit_ablation_20260907_thr58-60 报告
复算：配对t(delta_pp/t) 是否与报告一致
补测：1) 重叠观测(AC1/Newey-West) 对 t 值的高估程度
      2) 逐笔(trade-level) Welch 双样本检验（独立观测口径）
      3) 出场原因构成 / 口径自检基线 / live_trail 回撤与 Calmar
只读回测，不写任何生产文件。
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

PROJ = r"D:\QuantLab\projects\Project_16_LightGBM股票大师"
DATA = os.path.join(PROJ, "data")
REAL = os.path.join(DATA, "real")
sys.path.insert(0, PROJ)
os.chdir(PROJ)

os.environ["BT_SLIPS"] = "0.001"
os.environ["BT_N_LIST"] = "5,10"

import scan_rotate_cost_real as BT  # noqa: E402

MODES = [
    ("none", "无止损(纯alpha上界)", "none", 0.0, 0.0),
    ("fixed", "固定-7%/+15%(回测原口径)", "fixed", 0.0, 0.0),
    ("live_trail", "实盘口径:-7%+8%追盈+15%", "live_trail", 0.0, 0.0),
    ("atr25", "ATR自适应 2.0/2.5", "atr", 2.0, 2.5),
    ("atr20", "ATR自适应 2.0/2.0", "atr", 2.0, 2.0),
    ("atr30", "ATR自适应 3.0/3.0", "atr", 3.0, 3.0),
    ("time", "时间止损(过半不盈换股)", "time", 0.0, 0.0),
    ("ma5", "跌破MA5 + -7%止损", "ma", 0.0, 0.0),
]


def run_one(dates, per_day, market_daily, open_map, N, slip, mode, k1, k2):
    BT.EXIT_MODE = mode
    BT.ATR_K1, BT.ATR_K2 = k1, k2
    BT.SELL_SKIP_DOWN[0] = 0
    BT.SELL_DELIST[0] = 0
    BT.EXIT_REASON_CNT.clear()
    trades, daily_ret, n_skip = BT.simulate(dates, per_day, N, slip, open_map, exec_ok=True)
    s = BT.stats(trades, daily_ret, market_daily)
    hold_days = [t[1] - t[0] - 1 for t in trades]
    fwd = pd.Series([t[2] for t in trades]) if trades else pd.Series(dtype=float)
    mdd = abs(float(s["max_drawdown"])) or 1e-9
    reasons = dict(BT.EXIT_REASON_CNT)
    return {
        "N": N, "mode": mode, "n_trades": len(trades),
        "daily_excess": s["daily_excess"],
        "win_rate": s["win_rate"], "pl_ratio": s["profit_loss_ratio"],
        "mdd": s["max_drawdown"],
        "calmar": float(s["daily_excess"]) * 252 / mdd,
        "avg_hold": float(np.mean(hold_days)) if hold_days else np.nan,
        "mean_ret": float(fwd.mean()) if len(fwd) else np.nan,
        "std_ret": float(fwd.std()) if len(fwd) > 1 else np.nan,
        "pct_lt_stop": float((fwd <= -0.07).mean()) if len(fwd) else np.nan,
        "reasons": "; ".join("%s:%d" % (k, v) for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
        "_daily_ret": pd.Series(daily_ret).reset_index(drop=True),
        "_fwd": fwd.reset_index(drop=True),
    }


def paired_t(a, b):
    if a is None or b is None or len(a) != len(b) or len(a) < 10:
        return np.nan, np.nan
    d = (a - b).dropna()
    if len(d) < 10 or d.std() == 0:
        return np.nan, np.nan
    return float(d.mean() * 100), float(d.mean() / (d.std(ddof=1) / np.sqrt(len(d))))


def ac1(x):
    v = x.dropna().values.astype(float)
    if len(v) < 30:
        return np.nan
    v = v - v.mean()
    denom = np.dot(v, v)
    if denom == 0:
        return 0.0
    return float(np.dot(v[:-1], v[1:]) / denom)


def nw_t(d, lag):
    x = d.dropna().values.astype(float)
    n = len(x)
    xm = x - x.mean()
    g0 = float(np.mean(xm ** 2))
    s = g0
    for j in range(1, lag + 1):
        w = 1 - j / (lag + 1)
        s += 2 * w * float(np.mean(xm[j:] * xm[:-j]))
    se = np.sqrt(s / n)
    return float(d.mean() / se) if se > 0 else np.nan


def welch(a, b):
    a = a.dropna().values.astype(float)
    b = b.dropna().values.astype(float)
    n1, n2 = len(a), len(b)
    if n1 < 5 or n2 < 5:
        return np.nan, np.nan
    m1, m2 = a.mean(), b.mean()
    v1, v2 = a.var(ddof=1), b.var(ddof=1)
    se = np.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return np.nan, np.nan
    t = (m1 - m2) / se
    df = (v1 / n1 + v2 / n2) ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    return float(t), float(df)


def main():
    CACHE = os.path.join(PROJ, "verify_exit_ablation_cache.pkl")
    if os.path.exists(CACHE):
        print("[0/3] 命中缓存，跳过 build_per_day ...")
        with open(CACHE, "rb") as f:
            series, trades_map, df = pickle.load(f)
        print("    缓存档数:", sorted(series.keys()))
    else:
        print("[1/3] build_per_day ...")
        dates, per_day, market_daily, open_map = BT.build_per_day()
        print("    测试期 %s ~ %s | %d 日 | 滑点0.1%% | TOP=%d | 红线默认%.0f"
              % (dates[0].date(), dates[-1].date(), len(dates), BT.TOP, BT.THRESHOLD))

        print("[2/3] 跑 4 档 x 8 模式 ...")
        series, trades_map = {}, {}
        rows = []
        for thr in (58.0, 60.0):
            BT.THRESHOLD = thr
            for N in (5, 10):
                series[(thr, N)] = {}
                trades_map[(thr, N)] = {}
                for mode, label, em, k1, k2 in MODES:
                    r = run_one(dates, per_day, market_daily, open_map, N, 0.001, em, k1, k2)
                    r["label"], r["thr"] = label, thr
                    rows.append(r)
                    series[(thr, N)][mode] = r.pop("_daily_ret")
                    trades_map[(thr, N)][mode] = r.pop("_fwd")
                    print("    thr%.0f N=%-2d %-26s 交易%4d 日超额%+.3f%% 胜率%.1f%% 回撤%.1f%% Calmar%.2f 均持有%.2f日"
                          % (thr, N, label, r["n_trades"], r["daily_excess"] * 100,
                             r["win_rate"] * 100, r["mdd"] * 100, r["calmar"], r["avg_hold"]))

        df = pd.DataFrame(rows)
        with open(CACHE, "wb") as f:
            pickle.dump((series, trades_map, df), f)
        print("    缓存已落盘:", CACHE)

    print("[3/3] 复算 + 修正 ...")
    out = []
    n_cmp = 0
    for thr in (58.0, 60.0):
        for N in (5, 10):
            base = series[(thr, N)]["fixed"]
            base_tr = trades_map[(thr, N)]["fixed"]
            for mode, label, em, k1, k2 in MODES:
                if mode == "fixed":
                    continue
                n_cmp += 1
                a = series[(thr, N)][mode]
                d_pp, t = paired_t(a, base)
                d = (a - base).dropna()
                rho1 = ac1(d)
                n_eff = len(d) * (1 - rho1) / (1 + rho1) if rho1 is not None and not np.isnan(rho1) else np.nan
                t_nw = nw_t(d, lag=10)
                tr_a = trades_map[(thr, N)][mode]
                t_welch, df_w = welch(tr_a, base_tr)
                md_welch = (tr_a.mean() - base_tr.mean()) * 100 if len(tr_a) and len(base_tr) else np.nan
                out.append({
                    "thr": thr, "N": N, "mode": mode, "label": label,
                    "delta_pp_report": d_pp, "t_report": t,
                    "n_pairs": len(d), "rho1": rho1, "n_eff": n_eff,
                    "t_nw_lag10": t_nw,
                    "n_tr_mode": len(tr_a), "n_tr_fixed": len(base_tr),
                    "trade_md_pp": md_welch, "t_welch": t_welch, "welch_df": df_w,
                })
    sig = pd.DataFrame(out)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)

    print("\n=== A. 复算一致性: 报告 t 值 vs 本次复算 ===")
    csv = pd.read_csv(os.path.join(REAL, "exit_ablation_20260907_thr58-60_significance.csv"))
    m = pd.merge(sig, csv[["thr", "N", "mode", "t", "delta_pp"]],
                 on=["thr", "N", "mode"], suffixes=("_recalc", "_report"))
    m["t_diff"] = (m["t_report"] - m["t"]).abs()
    m["pp_diff"] = (m["delta_pp_report"] - m["delta_pp"]).abs()
    print("max |t 偏差| = %.6f   max |delta_pp 偏差| = %.6f" % (m["t_diff"].max(), m["pp_diff"].max()))
    mbad = m[(m["t_diff"] > 0.001) | (m["pp_diff"] > 0.0001)]
    if len(mbad):
        print("!! 不一致行:\n", mbad[["thr", "N", "mode", "t_report", "t", "delta_pp_report", "delta_pp"]].to_string(index=False))
    else:
        print("全 28 行复算一致（容差内）")

    print("\n=== B. 重叠观测修正: 配对日差序列 AC1 / 有效样本 / Newey-West t ===")
    print(sig[["thr", "N", "mode", "t_report", "n_pairs", "rho1", "n_eff", "t_nw_lag10"]].to_string(index=False))

    print("\n=== C. 逐笔口径（独立观测近似）: mode交易 vs fixed交易 ===")
    print(sig[["thr", "N", "mode", "n_tr_mode", "n_tr_fixed", "trade_md_pp", "t_welch"]].to_string(index=False))

    print("\n=== D. 出场原因构成(复算) ===")
    for _, r in df.iterrows():
        print("thr%.0f N=%-2d %-24s %s" % (r["thr"], r["N"], r["mode"], r["reasons"]))

    print("\n=== E. 口径自检: fixed vs 既有基线 ===")
    base_map = {(58, 5): -0.039, (58, 10): -0.027}
    for thr in (58.0, 60.0):
        for N in (5, 10):
            v = df[(df["N"] == N) & (df["thr"] == thr) & (df["mode"] == "fixed")]["daily_excess"].iloc[0] * 100
            b = base_map.get((int(thr), N))
            print("thr%.0f N=%d fixed=%+.3f%% 基线=%s 偏差=%s" % (
                thr, N, v, ("%+.3f%%" % b) if b is not None else "NA",
                ("%+.3fpp" % (v - b)) if b is not None else "NA"))

    print("\n=== F. live_trail vs fixed: 回撤/Calmar/日超额 ===")
    for thr in (58.0, 60.0):
        for N in (5, 10):
            f = df[(df["N"] == N) & (df["thr"] == thr) & (df["mode"] == "fixed")].iloc[0]
            l = df[(df["N"] == N) & (df["thr"] == thr) & (df["mode"] == "live_trail")].iloc[0]
            print("thr%.0f N=%d  fixed: 回撤%+.1f%% Calmar%+.2f 日超额%+.3f%% | live_trail: 回撤%+.1f%% Calmar%+.2f 日超额%+.3f%%"
                  % (thr, N, f["mdd"] * 100, f["calmar"], f["daily_excess"] * 100,
                     l["mdd"] * 100, l["calmar"], l["daily_excess"] * 100))

    print("\n总比较次数:", n_cmp)


if __name__ == "__main__":
    main()
