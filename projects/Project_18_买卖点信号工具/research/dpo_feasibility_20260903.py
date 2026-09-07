# coding: utf-8
"""DPO 指标抓主升浪 —— 头条文章宣称可行性实证评估（2026-09-03）。

文章宣称（https://m.toutiao.com/is/fsS9l4M3bYs/）：
  - DPO 周期 12 / 信号线 MA6，抓主升浪"胜率高达 90%"
  - 买点1 起爆点：零下金叉 → 5日内上穿零轴站稳 + 股价站上MA20 + 放量≥前5日均量30%
  - 买点2 加速点：零轴上方二次金叉（洗盘不破零轴）+ 突破平台 + 放量
  - 铁律：零轴下方金叉"全是弱反弹"；卖出：破零轴清仓 / 连续3日跌破MA20清仓

评估范式：与 buypoint_signals.py 同源（PIT 向量化信号 + 前向收益统计 + 逐笔交易模拟）。
  - 价格用后复权(hfq)连续价，指标全部只用 t 日及以前数据（无未来函数）
  - 信号 t 日收盘产生，交易按 t+1 开盘价成交（含隔夜跳空， Momentum 类信号的真实代价）
  - 涨停开盘不可买（主板 9.5% / 创业板科创板 19.5%）、停牌缺口 >10 自然日剔除
  - 双边成本 0.15%

DPO 两种实现口径都测（文章未给公式，避免口径歧义质疑）：
  A. 标准去趋势：DPO = close − ref(MA(close,12), 7)     （shift = N/2+1）
  B. 文章字面：  DPO = close − MA(close,12)              （无移位的偏离式）

基线：全市场无条件（非ST、有成交）同周期前向收益命中率，替代不可用的指数超额。

备用数据源：按全局规则先调 data/gpsj_reader.is_available()，不可用则 [SKIP]
并在报告标注「单源结论（仅 astock），未经备用源交叉验证」。

用法：
  python research/dpo_feasibility_20260903.py [--start 2015-01-01] [--end 2026-08-21]
"""
import argparse
import gc
import json
import os
import time

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ASTOCK_DAILY = "D:/astock/daily/stock_daily.parquet"
RESULTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/results"

DPO_PERIOD = 12
DPO_SHIFT = DPO_PERIOD // 2 + 1          # 标准口径移位 = 7
SIG_PERIOD = 6                            # 文章：信号线 MA6
HOLD_CAP = 60                             # 仓库风控红线：最长持有 60 交易日
ROUND_COST = 0.0015                       # 双边成本

FWD_DAYS = (5, 10, 20, 60)


# ---------------------------------------------------------------------------
# gpsj 备用源可用性（全局规则：不可用必须显式标注，不静默）
# ---------------------------------------------------------------------------
def check_gpsj():
    try:
        import sys
        sys.path.insert(0, "D:/QuantLab")
        from data.gpsj_reader import is_available
        ok = bool(is_available())
    except Exception as e:
        print("[gpsj] reader 不可用: %s" % e)
        return False
    print("[gpsj] is_available = %s" % ok)
    return ok


# ---------------------------------------------------------------------------
# 数据加载（与 buypoint_signals.load_stock_panel 同口径，hfq）
# ---------------------------------------------------------------------------
NEED_COLS = [
    "trade_date", "ts_code", "open", "high", "low", "close",
    "vol", "adj_factor", "is_st",
]


def load_stock_panel(start, end):
    t0 = time.time()
    df = pq.read_table(ASTOCK_DAILY, columns=NEED_COLS).to_pandas()
    df = df.reset_index()
    df = df.dropna(subset=["trade_date", "ts_code"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    df = df[(df["trade_date"] >= pd.Timestamp(start))
            & (df["trade_date"] <= pd.Timestamp(end))]
    # 剔除北交所（30cm 涨跌幅与文章受众不符），剔除 ST 由信号层做（保留面板完整性）
    df = df[~df["ts_code"].str.endswith(".BJ")]
    df = df.sort_values(["ts_code", "trade_date"]).set_index(["trade_date", "ts_code"])
    adj = df["adj_factor"].astype("float32")
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype("float32") * adj
    df["vol"] = df["vol"].astype("float64")
    df["is_st"] = df["is_st"].fillna(0).astype("int8")
    print("[load] %d rows, %d codes, %.1fs"
          % (len(df), df.index.get_level_values("ts_code").nunique(), time.time() - t0))
    return df


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def gshift(s, n):
    return s.groupby(level="ts_code").shift(n)


def days_since(bool_series):
    """BARSLAST：距最近一次 True 的天数（无历史为 NaN）。"""
    tmp = pd.DataFrame({"b": bool_series.astype("int8"),
                        "code": bool_series.index.get_level_values("ts_code")})
    cum = tmp.groupby("code")["b"].transform(lambda x: x.cumsum())
    inner = tmp.groupby("code")["b"].transform(
        lambda x: x.groupby((x == 1).cumsum()).cumcount())
    out = inner.astype("float32")
    out = out.where(cum > 0)
    return out


# ---------------------------------------------------------------------------
# DPO 指标与信号（全部 PIT：只用 t 及以前数据）
# ---------------------------------------------------------------------------
def build_dpo(df, mode):
    close = df["close"].astype("float64")
    g = close.groupby(level="ts_code")
    ma12 = g.transform(lambda x: x.rolling(DPO_PERIOD, min_periods=DPO_PERIOD).mean())
    if mode == "std":
        dpo = close - gshift(ma12, DPO_SHIFT)
    else:
        dpo = close - ma12
    sig = dpo.groupby(level="ts_code").transform(
        lambda x: x.rolling(SIG_PERIOD, min_periods=SIG_PERIOD).mean())
    return dpo, sig


def build_common(df):
    close = df["close"].astype("float64")
    vol = df["vol"]
    g = close.groupby(level="ts_code")
    ma20 = g.transform(lambda x: x.rolling(20, min_periods=20).mean())
    vol_ma5 = vol.groupby(level="ts_code").transform(
        lambda x: x.rolling(5, min_periods=5).mean())
    vol_ratio = vol / gshift(vol_ma5, 1)
    hhv_c10_prev = gshift(close.groupby(level="ts_code").transform(
        lambda x: x.rolling(10, min_periods=10).max()), 1)
    below = close < ma20
    below3 = below & gshift(below, 1).astype("bool") & gshift(below, 2).astype("bool")
    return ma20, vol_ratio, hhv_c10_prev, below3


# ---------------------------------------------------------------------------
# 前向收益统计
# ---------------------------------------------------------------------------
def fwd_stats(sig, close, base_sample_ret=None, fwd_days=FWD_DAYS):
    sig_idx = sig.index[sig.fillna(False)]
    if len(sig_idx) == 0:
        return None
    out = pd.DataFrame(index=sig_idx)
    for n in fwd_days:
        fwd = close.groupby(level="ts_code").shift(-n)
        ret = (fwd / close - 1.0) * 100.0
        out["fwd%d" % n] = ret.reindex(sig_idx)
        del fwd, ret
        gc.collect()
    return out


def summarize_fwd(name, out, baseline):
    if out is None or len(out) == 0:
        print("  [%s] 无信号" % name)
        return None
    res = {"name": name, "n": int(len(out))}
    print("\n【%s】 信号数: %d" % (name, len(out)))
    for n in FWD_DAYS:
        v = out["fwd%d" % n].dropna()
        if len(v) == 0:
            continue
        hit = (v > 0).mean() * 100.0
        b = baseline["fwd%d" % n]
        print("  fwd%-3d 均值%+6.2f%% 中位%+6.2f%% 命中%5.1f%% (基线%.1f%%, Δ%+5.1fpp) "
              "≥10%%: %4.1f%% ≥20%%: %4.1f%%"
              % (n, v.mean(), v.median(), hit, b, hit - b,
                 (v >= 10).mean() * 100.0, (v >= 20).mean() * 100.0))
        res["fwd%d" % n] = {"mean": round(float(v.mean()), 2),
                            "median": round(float(v.median()), 2),
                            "hit": round(float(hit), 1),
                            "hit_ge10": round(float((v >= 10).mean() * 100.0), 1),
                            "hit_ge20": round(float((v >= 20).mean() * 100.0), 1)}
    out2 = out.copy()
    out2["year"] = out2.index.get_level_values("trade_date").year
    ytab = out2.groupby("year")["fwd20"].agg(["count", "mean",
                                              lambda x: (x > 0).mean() * 100.0])
    ytab.columns = ["n", "fwd20_mean", "hit20"]
    print("  年度 fwd20 命中率:")
    print(ytab.round(2).to_string())
    res["yearly"] = {str(k): {"n": int(r["n"]), "mean": round(float(r["fwd20_mean"]), 2),
                              "hit": round(float(r["hit20"]), 1)}
                     for k, r in ytab.iterrows()}
    return res


def baseline_stats(close, st_mask, sample_rows=400000):
    """全市场无条件基线（非ST）：系统抽样（每逢5个交易日取1日）控制计算量。"""
    dates = close.index.get_level_values("trade_date").unique().sort_values()
    keep = set(dates[::5])
    mask = close.index.get_level_values("trade_date").isin(keep) & (~st_mask.values)
    sub_idx = close.index[mask]
    base = {}
    for n in FWD_DAYS:
        fwd = close.groupby(level="ts_code").shift(-n)
        ret = (fwd / close - 1.0) * 100.0
        v = ret.reindex(sub_idx).dropna()
        base["fwd%d" % n] = round(float((v > 0).mean() * 100.0), 1)
        base["fwd%d_mean" % n] = round(float(v.mean()), 2)
        del fwd, ret
        gc.collect()
    print("\n[基线] 全市场非ST样本 %d 行（1/5交易日抽样）: %s" % (len(sub_idx), base))
    return base


# ---------------------------------------------------------------------------
# 逐笔交易模拟（文章自身卖出纪律 + 仓库 60 日持有红线）
# ---------------------------------------------------------------------------
def simulate(sig, df, dpo, below3, hold_cap=HOLD_CAP, cost=ROUND_COST):
    """信号 t 收盘 → t+1 开盘买入；破零轴 / 连续3日收盘<MA20 / 满60日 → 当日收盘卖出。
    涨停开盘不可买；停牌缺口 >10 自然日剔除；同股不重叠持仓。"""
    work = pd.DataFrame({
        "open": df["open"].astype("float64"),
        "high": df["high"].astype("float64"),
        "close": df["close"].astype("float64"),
        "adj": df["adj_factor"].astype("float64"),
        "dpo": dpo.astype("float64"),
        "below3": below3.astype("bool"),
        "sig": sig.astype("bool"),
    })
    work = work.swaplevel().sort_index()          # (ts_code, trade_date)
    records = []
    for code, g in work.groupby(level=0, sort=False):
        arrs = {c: g[c].to_numpy() for c in work.columns}
        dates = g.index.get_level_values(1).values
        n = len(g)
        lim = 0.195 if code[:3] in ("300", "301", "688", "689") else 0.095
        sig_pos = np.where(arrs["sig"] & np.isfinite(arrs["dpo"]))[0]
        busy_until = -1
        for i in sig_pos:
            if i <= busy_until or i + 1 >= n:
                continue
            e = i + 1
            if not np.isfinite(arrs["adj"][e]) or arrs["adj"][e] <= 0:
                continue
            if (np.datetime64(dates[e]) - np.datetime64(dates[i])) > np.timedelta64(10, "D"):
                continue
            prev_raw_close = arrs["close"][i] / arrs["adj"][i]
            raw_open = arrs["open"][e] / arrs["adj"][e]
            if prev_raw_close > 0 and raw_open / prev_raw_close - 1.0 >= lim:
                continue                            # 涨停开盘买不进
            entry_px = arrs["open"][e]
            if not np.isfinite(entry_px) or entry_px <= 0:
                continue
            hi = entry_px
            exit_j = n - 1
            for j in range(e, min(e + hold_cap + 1, n)):
                hj = arrs["high"][j]
                if np.isfinite(hj) and hj > hi:
                    hi = hj
                if arrs["dpo"][j] < 0 or arrs["below3"][j] or (j - e) >= hold_cap:
                    exit_j = j
                    break
            busy_until = exit_j
            ret = arrs["close"][exit_j] / entry_px - 1.0 - cost
            mfe = hi / entry_px - 1.0
            records.append((code, pd.Timestamp(dates[i]), pd.Timestamp(dates[exit_j]),
                            ret * 100.0, mfe * 100.0, exit_j - e))
    tr = pd.DataFrame(records, columns=["code", "entry", "exit", "ret", "mfe", "hold"])
    return tr


def summarize_trades(name, tr):
    if len(tr) == 0:
        print("  [%s] 无成交" % name)
        return None
    win = (tr["ret"] > 0).mean() * 100.0
    print("\n【逐笔模拟 %s】 交易数: %d" % (name, len(tr)))
    print("  胜率(净收益>0): %.1f%%   平均%+.2f%%  中位%+.2f%%"
          % (win, tr["ret"].mean(), tr["ret"].median()))
    print("  ≥10%%: %.1f%%   ≥20%%: %.1f%%   持期内最高浮盈≥20%%(MFE): %.1f%%"
          % ((tr["ret"] >= 10).mean() * 100.0, (tr["ret"] >= 20).mean() * 100.0,
             (tr["mfe"] >= 20).mean() * 100.0))
    print("  平均持有 %.1f 交易日   亏损交易均亏 %+.2f%%  盈利交易均赚 %+.2f%%"
          % (tr["hold"].mean(), tr.loc[tr["ret"] <= 0, "ret"].mean(),
             tr.loc[tr["ret"] > 0, "ret"].mean()))
    tr2 = tr.copy()
    tr2["year"] = tr2["entry"].dt.year
    ytab = tr2.groupby("year")["ret"].agg(["count", "mean",
                                           lambda x: (x > 0).mean() * 100.0])
    ytab.columns = ["n", "ret_mean", "win"]
    print("  年度胜率:")
    print(ytab.round(2).to_string())
    res = {"name": name, "n_trades": int(len(tr)),
           "win": round(float(win), 1), "ret_mean": round(float(tr["ret"].mean()), 2),
           "ret_median": round(float(tr["ret"].median()), 2),
           "ge10": round(float((tr["ret"] >= 10).mean() * 100.0), 1),
           "ge20": round(float((tr["ret"] >= 20).mean() * 100.0), 1),
           "mfe_ge20": round(float((tr["mfe"] >= 20).mean() * 100.0), 1),
           "avg_hold": round(float(tr["hold"].mean()), 1),
           "yearly": {str(k): {"n": int(r["n"]), "mean": round(float(r["ret_mean"]), 2),
                               "win": round(float(r["win"]), 1)}
                      for k, r in ytab.iterrows()}}
    return res


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run(start_eval, end_eval):
    gpsj_ok = check_gpsj()
    if not gpsj_ok:
        print("[gpsj][SKIP] 备用数据源不可用 → 单源结论（仅 astock），未经备用源交叉验证")

    # 加载评估期 + 前置 90 自然日 warm-up（最长滚动窗 25 日 × 安全系数）
    load_start = (pd.Timestamp(start_eval) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    df = load_stock_panel(load_start, end_eval)
    st_mask = df["is_st"] == 1

    print("[ind] 计算通用指标…")
    t0 = time.time()
    ma20, vol_ratio, hhv_c10_prev, below3 = build_common(df)
    print("[ind] %.1fs" % (time.time() - t0))

    close = df["close"].astype("float64")
    vol_ok = df["vol"] > 0
    eval_mask = (df.index.get_level_values("trade_date") >= pd.Timestamp(start_eval)) & vol_ok

    baseline = baseline_stats(close, st_mask)

    results = {"period": [start_eval, end_eval], "baseline": baseline,
               "gpsj_cross_validated": gpsj_ok, "signals": [], "trades": []}

    for mode, mode_name in [("std", "标准DPO(ref-MA12,7)"), ("bias", "偏离式(close-MA12)")]:
        print("\n" + "=" * 72)
        print("=== DPO 口径：%s ===" % mode_name)
        dpo, sig = build_dpo(df, mode)

        dpo_p1 = gshift(dpo, 1)
        sig_p1 = gshift(sig, 1)
        gcross = (dpo > sig) & (dpo_p1 <= sig_p1)
        zc_up = (dpo > 0) & (dpo_p1 <= 0)
        gc_below = gcross & (dpo_p1 <= 0)
        base_cond = (ma20.notna()) & (~st_mask) & (vol_ratio >= 1.3) & eval_mask

        sig_a = zc_up & (dpo > sig) & (days_since(gc_below) <= 5) \
            & (close > ma20) & base_cond
        gc_above = gcross & (dpo > 0)
        gc_cnt25 = gc_above.astype("int8").groupby(level="ts_code").transform(
            lambda x: x.rolling(25, min_periods=1).sum())
        dpo_min25 = dpo.groupby(level="ts_code").transform(
            lambda x: x.rolling(25, min_periods=25).min())
        sig_b = gcross & (dpo > 0) & (gc_cnt25 >= 2) & (dpo_min25 > 0) \
            & (close > hhv_c10_prev) & base_cond
        g_below_raw = gc_below & base_cond & (ma20.notna())
        g_above_raw = gc_above & base_cond & (ma20.notna()) & eval_mask

        for nm, s in [("A_起爆点", sig_a), ("B_零上二次金叉", sig_b),
                      ("对照_零下金叉", g_below_raw), ("对照_零上金叉裸", g_above_raw)]:
            r = summarize_fwd("%s|%s" % (mode_name, nm), fwd_stats(s, close), baseline)
            if r:
                r["dpo_mode"] = mode
                results["signals"].append(r)

        for nm, s in [("A_起爆点", sig_a), ("B_零上二次金叉", sig_b),
                      ("对照_零上金叉裸", g_above_raw)]:
            tr = simulate(s, df, dpo, below3)
            r = summarize_trades("%s|%s" % (mode_name, nm), tr)
            if r:
                r["dpo_mode"] = mode
                results["trades"].append(r)

        del dpo, sig, gcross, zc_up, gc_below, gc_above, gc_cnt25, dpo_min25, dpo_p1, sig_p1
        gc.collect()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = RESULTS_DIR + "/DPO可行性评估_20260903.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n[out] %s" % out_json)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="2026-08-21")
    args = ap.parse_args()
    run(args.start, args.end)


if __name__ == "__main__":
    main()
