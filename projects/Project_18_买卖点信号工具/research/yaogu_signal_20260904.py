# coding: utf-8
"""妖股起飞暗号 —— 头条文章策略可行性评估（2026-09-04）。

文章《花半年翻遍近千个涨停板，我挖出"妖股"起飞前唯一的共同暗号》
（https://m.toutiao.com/is/Y4JyR7guC54/）宣称 5 道起飞前暗号 + 双针探底综合形态：

  暗号1 起飞前最后一次下蹲：成交量萎缩到前期活跃均量 50% 以下（缩量回踩）
  暗号2 启动前 3 日内至少一次长下影线，影线 ≥ 实体 2 倍，且次日收盘不创新低
  暗号3 盘整末端连续两根平量小阳线（收盘几乎同价），随后放量跃过两日最高价
  暗号4 启动当天集合竞价"量热价冷"：竞价量 ≥ 前日 2 倍但竞价涨幅压 ≤2%（需竞价数据）
  暗号5 起飞前 5 日内盘中急跌 >3% 且 15 分钟内收复（需分钟线数据）
  综合   双针探底变体：间隔 ≤3 日两根长下影，第二根低点更高且量递减，中阳越过触发

评估范式：与 dpo 系列同源（PIT 向量化信号 + 前向收益统计 + 逐笔交易模拟）。
  - 价格用后复权(hfq)，指标只用 t 日及以前数据（无未来函数）
  - 信号 t 日收盘产生，交易按 t+1 开盘价成交（含隔夜跳空）
  - 涨停开盘不可买（主板 9.5% / 创业板科创板 19.5%）、停牌缺口 >10 自然日剔除
  - 双边成本 0.15%；基线 = 全市场非ST无条件同周期命中率
  - 样本外分段：2015-2018 vs 2019-2025（P18 范式：区间符号翻转 = 无稳定 alpha）

暗号4（竞价量热价冷）、暗号5（盘中急跌急收 15 分钟收复）需要集合竞价/分钟线数据，
本脚本用日线数据无法验证，报告中标注数据限制（T-20260901-003 有竞价快照但仅近期，
全市场 12 年分钟级扫描计算量不可行）。

零主观定义（文章模糊处取可操作近似，参数均在报告中标注）：
  相对低位 low_pos = close ≤ 0.8 × 近60日最高（距60日高点回撤≥20%）
  暗号1: low_pos & vol < 0.5×前20日均量 & 收阴(close<prev_close)
  暗号2a: low_pos & 下影≥2×实体（当日）
  暗号2b: 暗号2a 的次日且 close ≥ 前日 low（次日收盘不创新低）
  暗号3: 连续两日小阳(0<涨≤2%) & 两日收盘差≤0.5% & 量比∈[0.8,1.2] & 当日放量(≥1.5×前5均量) & close>两日最高
  暗号6: 双针(当前长下影 & 近3日内另有长下影) & 当前low>近3日最低 & 量收缩(vol<前3日均量) & 中阳(涨≥2%) & close>近5日高点
  综合: s_any = 四暗号任一；s2b&s6 = 双针系共振

备用数据源：按全局规则先调 data/gpsj_reader.is_available()，不可用则 [SKIP] 并标注
「单源结论（仅 astock），未经备用源交叉验证」。

用法：
  python research/yaogu_signal_20260904.py [--start 2015-01-01] [--end 2026-08-21]
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
    check_gpsj, load_stock_panel, gshift, days_since,
    fwd_stats, summarize_fwd, baseline_stats, summarize_trades,
)

ROUND_COST = 0.0015
FWD_DAYS = (5, 10, 20, 60)
SEGS = (("2015-2018", "2015-01-01", "2018-12-31"),
        ("2019-2025", "2019-01-01", "2025-12-31"))


def build_indicators(df):
    """构造所有暗号信号（PIT 安全）。返回 dict[name -> bool Series]。"""
    open_ = df["open"].astype("float64")
    high = df["high"].astype("float64")
    low = df["low"].astype("float64")
    close = df["close"].astype("float64")
    vol = df["vol"].astype("float64")
    st_mask = df["is_st"] == 1
    vol_ok = vol > 0
    g = close.groupby(level="ts_code")
    base_ok = vol_ok & (~st_mask)

    hhv60 = gshift(g.transform(lambda x: x.rolling(60, min_periods=60).max()), 1)
    low_pos = (close <= 0.8 * hhv60) & hhv60.notna()

    vol_ma20_prev = gshift(vol.groupby(level="ts_code").transform(
        lambda x: x.rolling(20, min_periods=20).mean()), 1)
    vol_ma5_prev = gshift(vol.groupby(level="ts_code").transform(
        lambda x: x.rolling(5, min_periods=5).mean()), 1)

    prev_close = gshift(close, 1)
    body = (close - open_).abs()
    wick_low = open_.where(open_ < close, close) - low          # min(open,close)-low
    wick2body = (body > 0) & (wick_low >= 2.0 * body)

    up_small = (close > open_) & (close / prev_close - 1.0 > 0) \
        & (close / prev_close - 1.0 <= 0.02)
    flat_close = ((close - prev_close).abs() / prev_close <= 0.005)
    vol_ratio_12 = vol / gshift(vol, 1)
    flat_vol = vol_ratio_12.between(0.8, 1.2)

    # 暗号3 触发：突破日放量 & close 越过两根小阳最高价（前两日 high）
    # 两根小阳线（t-2, t-1）：收盘接近 & 量接近；突破日（t）：放量 & close>前两日最高
    flat_close_pair = gshift(flat_close, 1)               # t-1 与 t-2 收盘接近
    flat_vol_pair = gshift(vol_ratio_12, 1).between(0.8, 1.2)  # t-1 与 t-2 量平
    hhv2_prev = np.maximum(gshift(high, 1), gshift(high, 2))   # 前两日最高价
    s3_trigger = (vol >= 1.5 * vol_ma5_prev) & (close > hhv2_prev)
    s3 = gshift(up_small, 1) & gshift(up_small, 2) \
        & flat_close_pair & flat_vol_pair & s3_trigger

    # 暗号6：双针探底 + 中阳突破
    wick_recent = wick2body | gshift(wick2body, 1) | gshift(wick2body, 2) | gshift(wick2body, 3)
    low3_prev = gshift(low.groupby(level="ts_code").transform(
        lambda x: x.rolling(3, min_periods=3).min()), 1)
    vol_ma3_prev = gshift(vol.groupby(level="ts_code").transform(
        lambda x: x.rolling(3, min_periods=3).mean()), 1)
    hhv5_prev = gshift(g.transform(lambda x: x.rolling(5, min_periods=5).max()), 1)
    yang_break = (close > open_) & (close / prev_close - 1.0 >= 0.02) & (close > hhv5_prev)
    s6 = wick2body & wick_recent & (low > low3_prev) & (vol < vol_ma3_prev) \
        & yang_break

    signals = {
        "暗号1_缩量回踩": low_pos & (vol < 0.5 * vol_ma20_prev) & (close < prev_close) & base_ok,
        "暗号2a_长下影": low_pos & wick2body & base_ok,
        "暗号2b_长下影次日不创新低": gshift(wick2body, 1) & (close >= gshift(low, 1)) \
            & low_pos & base_ok,
        "暗号3_平量小阳放量突破": s3 & low_pos & base_ok,
        "暗号6_双针探底中阳突破": s6 & low_pos & base_ok,
        "对照_低位中阳突破动量": yang_break & low_pos & base_ok,
        "综合_任一暗号": None,   # 下面由 OR 计算
        "综合_双针系共振": s6 & gshift(wick2body, 1) & base_ok,
    }
    signals["综合_任一暗号"] = (signals["暗号1_缩量回踩"] | signals["暗号2a_长下影"]
                              | signals["暗号3_平量小阳放量突破"]
                              | signals["暗号6_双针探底中阳突破"]) & base_ok
    # 与"综合_双针系共振"不同的表述：暗号6 已含双针，双针系共振 = 6 & 近一日仍有长下影
    return signals


def simulate_fixed(sig, df, hold=20, cost=ROUND_COST):
    """信号 t 收盘 → t+1 开盘买入；持满 hold 交易日收盘卖出。
    涨停开盘不可买；停牌缺口 >10 自然日剔除；同股不重叠持仓。"""
    work = pd.DataFrame({
        "open": df["open"].astype("float64"),
        "high": df["high"].astype("float64"),
        "close": df["close"].astype("float64"),
        "adj": df["adj_factor"].astype("float64"),
        "sig": sig.astype("bool"),
    }).swaplevel().sort_index()          # (ts_code, trade_date)
    records = []
    for code, g in work.groupby(level=0, sort=False):
        arrs = {c: g[c].to_numpy() for c in work.columns}
        dates = g.index.get_level_values(1).values
        n = len(g)
        lim = 0.195 if code[:3] in ("300", "301", "688", "689") else 0.095
        sig_pos = np.where(arrs["sig"])[0]
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
            xj = min(e + hold, n - 1)
            for j in range(e, xj + 1):
                hj = arrs["high"][j]
                if np.isfinite(hj) and hj > hi:
                    hi = hj
            exit_px = arrs["close"][xj]
            ret = exit_px / entry_px - 1.0 - cost
            mfe = hi / entry_px - 1.0
            records.append((code, pd.Timestamp(dates[i]), pd.Timestamp(dates[xj]),
                            ret * 100.0, mfe * 100.0, xj - e))
    return pd.DataFrame(records, columns=["code", "entry", "exit", "ret", "mfe", "hold"])


def run(start_eval, end_eval):
    gpsj_ok = check_gpsj()
    if not gpsj_ok:
        print("[gpsj][SKIP] 备用数据源不可用 → 单源结论（仅 astock），未经备用源交叉验证")

    load_start = (pd.Timestamp(start_eval) - pd.Timedelta(days=120)).strftime("%Y-%m-%d")
    df = load_stock_panel(load_start, end_eval)
    st_mask = df["is_st"] == 1
    close = df["close"].astype("float64")
    eval_mask = (df.index.get_level_values("trade_date") >= pd.Timestamp(start_eval)) \
        & (df["vol"] > 0)

    baseline = baseline_stats(close, st_mask)

    print("\n[信号] 构建暗号…")
    t0 = time.time()
    signals = build_indicators(df)
    print("[信号] %.1fs" % (time.time() - t0))

    results = {"period": [start_eval, end_eval], "baseline": baseline,
               "gpsj_cross_validated": gpsj_ok, "signals": [], "trades": [],
               "seg_fwd20": {}}

    for name, sig in signals.items():
        sig_eval = sig & eval_mask
        r = summarize_fwd("【%s】" % name, fwd_stats(sig_eval, close), baseline)
        if r:
            results["signals"].append(r)
        # 样本外分段 fwd20
        seg_rec = {}
        for seg_name, s, e in SEGS:
            seg_mask = (sig_eval.index.get_level_values("trade_date") >= pd.Timestamp(s)) \
                & (sig_eval.index.get_level_values("trade_date") <= pd.Timestamp(e))
            out = fwd_stats(sig_eval & seg_mask, close)
            if out is not None and len(out) > 0:
                v = out["fwd20"].dropna()
                seg_rec[seg_name] = {"n": int(len(v)),
                                     "hit": round(float((v > 0).mean() * 100.0), 1),
                                     "mean": round(float(v.mean()), 2)}
        results["seg_fwd20"][name] = seg_rec
        print("  [样本外 fwd20] %s: %s" % (name, seg_rec))
        del sig_eval
        gc.collect()

    # 逐笔模拟（固定持有 10/20 日；暗号6 与对照加成本×2 压力）
    for name in ("暗号1_缩量回踩", "暗号2b_长下影次日不创新低",
                 "暗号3_平量小阳放量突破", "暗号6_双针探底中阳突破",
                 "对照_低位中阳突破动量", "综合_任一暗号", "综合_双针系共振"):
        sig = (signals[name] & eval_mask)
        for hold in (10, 20):
            tr = simulate_fixed(sig, df, hold=hold)
            rr = summarize_trades("%s|固定持有%d日" % (name, hold), tr)
            if rr:
                rr["hold"] = hold
                results["trades"].append(rr)
            del tr
            gc.collect()
            if hold == 20 and name in ("暗号6_双针探底中阳突破",
                                       "对照_低位中阳突破动量"):
                tr2 = simulate_fixed(sig, df, hold=hold, cost=0.003)
                rr2 = summarize_trades("%s|固定持有%d日(成本×2)" % (name, hold), tr2)
                if rr2:
                    rr2["hold"] = hold
                    rr2["cost_x2"] = True
                    results["trades"].append(rr2)
                del tr2
                gc.collect()
        del sig
        gc.collect()

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_json = RESULTS_DIR + "/妖股起飞暗号_评估_20260904.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=float)
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
