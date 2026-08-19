# coding: utf-8
"""生成 Trae 主升浪启动前/中段选股信号表（每日 ATR% top16）供回测引擎查表。

PIT 安全：信号只用当日及之前数据（收盘后确认），引擎 next_open 成交。
输出：
  - signal_table_trae_top16.json：dict {date: [codes]}（codes 已按 20日ATR% 升序 top16）
  - market_ma200.json：dict {date: bool}（市场等权指数 > MA200）

选股规则：
  模式A（启动前·起爆前夜）—— A1 60日振幅<35% + A2 BOLL带宽收口 +
                                 A3 MACD零轴金叉(|DIF|<0.1 & |DEA|<0.1) + A4 量比>1.5
  模式B（主升浪中段）    —— B1 均线多头排列 + B2 近20日涨幅+10%~+40% +
                                 B3 换手率2%~10% + B4 非涨停(<9.5%)
  共同过滤              —— 非 ST + 上市>=252日 + 真实价<50 +
                                 ROE>0(PIT) + 120日振幅<5x
"""
import json
import sys
import time

sys.path.insert(0, "D:/QuantLab")
sys.path.insert(0, "D:/QuantLab/projects/Project_12_RPS主升浪/research")

import numpy as np
import pandas as pd

from huangshi_formula_scan import load_stock_panel, compute_indicators, gshift
from factors.fina import _FinaCache, ASTOCK_FINA_PATH

OUT_PATH = "D:/QuantLab/projects/Project_14_Trae选股驱动/research/signal_table_trae_top16.json"
OUT_MARKET = "D:/QuantLab/projects/Project_14_Trae选股驱动/research/market_ma200.json"

_FINA_CACHE = None


def _get_fina_cache():
    """Lazily build and reuse the fina PIT cache (one parquet load, all stocks)."""
    global _FINA_CACHE
    if _FINA_CACHE is None:
        _FINA_CACHE = _FinaCache(ASTOCK_FINA_PATH)
    return _FINA_CACHE


def atr_pct_series(ind, win=20):
    """20 日 ATR%（真实波幅均值/收盘价），按股票 group 计算。"""
    close = ind["close"]
    high = ind["high"]
    low = ind["low"]
    pc = close.groupby(level="ts_code").shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    atr = tr.groupby(level="ts_code").transform(
        lambda x: x.rolling(win, min_periods=win).mean())
    return atr / close * 100.0


def vectorized_roe_panel(ind_index):
    """Build PIT ROE panel aligned to ind_index (trade_date, ts_code).

    For each (date, code), return latest reported roe where end_date <= date
    (convention: report end_date == availability date, matching _FinaCache.asof).
    """
    cache = _get_fina_cache()
    dates = ind_index.get_level_values("trade_date")
    codes = ind_index.get_level_values("ts_code")
    # strftime for fast YYYYMMDD string conversion (matches fina 'ed' format)
    dates_str = dates.strftime("%Y%m%d")
    codes_arr = codes.values
    dates_arr = dates_str.values

    out = np.full(len(ind_index), np.nan, dtype=float)
    # Group positional indices by code (one pass)
    ser = pd.Series(np.arange(len(codes_arr)), index=codes)
    code_groups = ser.groupby(level=0).indices  # dict {code: array of positions}

    for code, idxs in code_groups.items():
        if code not in cache.by_code:
            continue
        ed = cache.by_code[code]["ed"]   # sorted ascending (object dtype strings)
        roe = cache.by_code[code]["roe"]
        d_strs = dates_arr[idxs]
        # default side='left' -> for date == ed[i] returns i, then -1 -> i-1
        # this matches _FinaCache.asof behavior exactly
        positions = np.searchsorted(ed, d_strs) - 1
        valid = positions >= 0
        if not np.any(valid):
            continue
        valid_pos = positions[valid]
        roe_vals = roe[valid_pos]
        # filter NaN
        not_nan = roe_vals == roe_vals
        if not np.any(not_nan):
            continue
        orig_indices = idxs[valid][not_nan]
        out[orig_indices] = roe_vals[not_nan].astype(float)

    return pd.Series(out, index=ind_index, name="roe_pit")


def compute_extra_indicators(df, ind):
    """Append custom indicators needed by Trae uptrend rules on top of ind."""
    t0 = time.time()
    g = df.groupby(level="ts_code")
    close = df["close"]
    vol = df["vol"].astype(float)

    # 60日/120日收盘最高最低（A1、MAX5 过滤）
    ind["hhv_c_60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).max())
    ind["llv_c_60"] = g["close"].transform(lambda x: x.rolling(60, min_periods=60).min())
    ind["hhv_c_120"] = g["close"].transform(lambda x: x.rolling(120, min_periods=120).max())
    ind["llv_c_120"] = g["close"].transform(lambda x: x.rolling(120, min_periods=120).min())

    # BOLL：MA20 ± 2*std(20)
    ma20 = ind["ma20"]
    std20 = g["close"].transform(lambda x: x.rolling(20, min_periods=20).std())
    ind["boll_mid"] = ma20
    ind["boll_upper"] = ma20 + 2.0 * std20
    ind["boll_lower"] = ma20 - 2.0 * std20
    bw = (ind["boll_upper"] - ind["boll_lower"]) / ind["boll_mid"]
    ind["boll_bw"] = bw
    ind["boll_bw_ma120"] = bw.groupby(level="ts_code").transform(
        lambda x: x.rolling(120, min_periods=120).mean())

    # 20日均量、量比（当日/前一日20日均量）
    ind["vol_ma20"] = g["vol"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    ind["vol_ratio_20"] = vol / gshift(ind["vol_ma20"], 1)

    # 20日前收盘、当日涨幅 %、近20日涨幅
    ind["close_20d_ago"] = gshift(close, 20)
    ind["ret_today_pct"] = (close / gshift(close, 1) - 1.0) * 100.0
    ind["ret_20d"] = (close / ind["close_20d_ago"] - 1.0)

    # 振幅比率
    ind["range_c_60"] = (ind["hhv_c_60"] - ind["llv_c_60"]) / ind["llv_c_60"]
    ind["range_c_120"] = (ind["hhv_c_120"] - ind["llv_c_120"]) / ind["llv_c_120"]

    # MACD 零轴金叉：DIF 上穿 DEA 且 |DIF|<0.1 且 |DEA|<0.1
    dif_prev = gshift(ind["dif"], 1)
    dea_prev = gshift(ind["dea"], 1)
    cross_up = (ind["dif"] > ind["dea"]) & (dif_prev <= dea_prev)
    near_zero = (ind["dif"].abs() < 0.1) & (ind["dea"].abs() < 0.1)
    ind["macd_zero_cross"] = cross_up & near_zero

    # 均线多头排列
    ind["ma_bull_align"] = (
        (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"]) &
        (ind["ma20"] > ind["ma60"])
    )

    # 上市天数（从数据起始累计的交易日数）
    ind["listed_days"] = g.cumcount() + 1

    # 真实价所需字段
    ind["adj_factor"] = df["adj_factor"].astype(float)
    ind["is_st"] = df["is_st"].fillna(0).astype(int)
    ind["turnover"] = df["turnover_rate"].astype(float)

    print("[extra_ind] %.1fs" % (time.time() - t0))
    return ind


def main():
    start, end = "2018-06-01", "2026-07-31"
    print("=== 生成 Trae 主升浪信号表 %s ~ %s ===" % (start, end))
    t0 = time.time()
    df = load_stock_panel(start, end)
    print("[load] %.1fs" % (time.time() - t0))
    ind = compute_indicators(df)
    print("[ind] %.1fs" % (time.time() - t0))

    ind = compute_extra_indicators(df, ind)
    print("[extra] %.1fs" % (time.time() - t0))

    atr = atr_pct_series(ind)
    print("[atr] %.1fs" % (time.time() - t0))

    print("[roe] building PIT ROE panel...")
    roe_pit = vectorized_roe_panel(ind.index)
    print("[roe] %.1fs, %d non-null" % (time.time() - t0, int(roe_pit.notna().sum())))

    # ---- 共通过滤 ----
    common = (
        (ind["is_st"] == 0) &                                      # 非 ST
        (ind["listed_days"] >= 252) &                             # 上市>=252日
        ((ind["close"] / ind["adj_factor"]) < 50.0) &            # 真实价<50
        (roe_pit > 0) &                                           # ROE>0 (PIT)
        (ind["range_c_120"] < 5.0)                                # 120日振幅<5x（非彩票股）
    )
    print("[filter] common passed: %d / %d rows" % (int(common.sum()), len(common)))

    # ---- 模式A：启动前·起爆前夜 ----
    mode_a = (
        (ind["range_c_60"] < 0.35) &                              # A1: 60日振幅<35%
        (ind["boll_bw"] < ind["boll_bw_ma120"] * 0.7) &          # A2: BOLL 带宽收口
        ind["macd_zero_cross"] &                                  # A3: MACD 零轴金叉
        (ind["vol_ratio_20"] > 1.5)                               # A4: 量比>1.5
    ) & common
    print("[signal] mode_a (起爆前夜): %d" % int(mode_a.sum()))

    # ---- 模式B：主升浪中段 ----
    mode_b = (
        ind["ma_bull_align"] &                                    # B1: 均线多头排列
        (ind["ret_20d"] >= 0.10) & (ind["ret_20d"] <= 0.40) &    # B2: 近20日涨幅 +10%~+40%
        (ind["turnover"] >= 2.0) & (ind["turnover"] <= 10.0) &    # B3: 换手率 2%~10%
        (ind["ret_today_pct"] < 9.5)                             # B4: 非涨停
    ) & common
    print("[signal] mode_b (主升中段): %d" % int(mode_b.sum()))

    sig = mode_a | mode_b
    print("[signal] union: %d" % int(sig.sum()))

    # 按 20日ATR% 升序，每日取 top16
    sig_rows = sig[sig]
    atr_sig = atr.loc[sig_rows.index]

    table = {}
    g_sig = sig_rows.groupby(level="trade_date")
    for date, idx in g_sig.groups.items():
        sub = atr_sig.loc[idx].dropna()
        if len(sub) == 0:
            continue
        top16 = sub.sort_values().index.get_level_values("ts_code").tolist()[:16]
        table[str(date)[:10]] = top16

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(table, f, ensure_ascii=False)
    n_days = len(table)
    n_sigs = sum(len(v) for v in table.values())
    print("信号表: %d 天, 共 %d 只次, 日均 %.1f (top16)"
          % (n_days, n_sigs, n_sigs / max(1, n_days)))
    if n_days > 0:
        print("样本日 %s: %s" % (list(table.keys())[0], table[list(table.keys())[0]]))

    # ---- 市场 MA200 门控表（与 529 同口径） ----
    close_wide = ind["close"].unstack("ts_code")
    daily_mean = close_wide.mean(axis=1, skipna=True)  # 每日全市场等权
    ma200 = daily_mean.rolling(200, min_periods=200).mean()
    market_ok = {}
    for d, v in daily_mean.items():
        m = ma200.get(d)
        ds = str(d)[:10]
        market_ok[ds] = bool(v > m) if m == m else True  # 数据不足 fail-open
    with open(OUT_MARKET, "w", encoding="utf-8") as f:
        json.dump(market_ok, f, ensure_ascii=False)
    print("市场MA200表: %d 天 (out=%s)" % (len(market_ok), OUT_MARKET))
    print("输出: %s" % OUT_PATH)
    print("[total] %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
