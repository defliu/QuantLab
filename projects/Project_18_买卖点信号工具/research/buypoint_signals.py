# coding: utf-8
"""买卖点信号工具 —— 4 类买点信号扫描（PIT 安全，策略潜力评估用）。

需求来源：飞书《A股买卖点信号提示 / 选股工具需求文档》（Chris，2026-03-16）。
本项目定位（B 方案）：先评估各买点模块"有没有作为策略的可能性"，
方法 = 信号扫描 + 前向收益/命中率统计（与 Project_12 huangshi_formula_scan 同范式）。

信号均为 PIT 安全向量化布尔 Series：
  - 价格用后复权(hfq)连续价（形态等价，收益可比）
  - 指标一律用"不含当日"的前值判断（gshift），杜绝未来函数
  - 信号日收盘产生信号，统计信号后 5/10/20/60 日收益与超额

4 个买点模块（与需求文档一一对应）：
  1. 箱体突破   signal_box_breakout   低位横盘 → 放量突破箱体上沿（强势）
  2. 杯柄图形   signal_cup_handle     低位横盘 → 挖坑 → 回坑沿 → 杯柄整理 → 启动
  3. 趋势低吸   signal_trend_pullback 均线多头 → 缩量回调不创新低 → 转强
  4. 二次启动   signal_second_launch  底部箱体 → 拉升≤30% → 半年新高 → 再整理 → 再启动

参数全部收敛在模块顶部 _DEFAULT_CFG，可被 runner 覆盖。

用法：
  python research/buypoint_signals.py [--start 2019-01-01] [--end 2025-12-31] [--quick]
"""
import argparse
import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ASTOCK_DAILY = "E:/astock/daily/stock_daily.parquet"
INDEX_DB = "F:/backtest_workspace/data/duckdb/benchmark_index.duckdb"
INDEX_CODE = "000001.SH"  # 上证指数（超额基准）

NEED_COLS = [
    "trade_date", "ts_code", "open", "high", "low", "close",
    "vol", "amount", "adj_factor", "turnover_rate", "circ_mv", "is_st",
]

# ---------------------------------------------------------------------------
# 默认参数（与需求文档"八、参数设置"对应，均可覆盖）
# ---------------------------------------------------------------------------
_DEFAULT_CFG = {
    # 通用
    "min_circ_mv": 0,           # 最小流通市值（万元），0=不限；200000=20亿
    "is_st_ok": 0,              # 是否允许 ST，0=剔除
    # 1. 箱体突破
    "box_period": 40,           # 箱体周期（横盘整理窗口）
    "box_max_range": 0.22,      # 箱体振幅上限（相对低位横盘）
    "low_pos_max": 0.45,        # 箱体在 120 日区间的位置分位上限（相对低位）
    "strong_gain": 0.03,        # 强势突破涨幅阈值
    "vol_ratio_min": 1.3,       # 突破放量倍数（当日量/前5日均量）
    # 2. 杯柄图形
    "cup_lookback": 60,         # 杯沿/坑底观察窗口
    "cup_depth_min": 0.12,      # 挖坑深度下限
    "cup_depth_max": 0.38,      # 挖坑深度上限
    "recover_ratio": 0.95,      # 回到坑沿比例
    "handle_window": 8,         # 杯柄整理窗口
    "handle_range": 0.10,       # 杯柄振幅上限
    # 3. 上升趋势回调低吸
    "pullback_max": 0.15,       # 回调深度上限（距近期高点）
    "pullback_min": 0.03,       # 回调深度下限（过滤仍在加速上涨）
    "hhv_ref": 20,              # 近期高点参考窗口
    "small_k_days": 3,          # 连续小K线（不创新低）天数
    "vol_low_ratio": 0.90,      # 缩量阈值（当日量/前5日均量）
    "macd_enabled": 0,          # 是否启用 MACD 金叉（需求文档勾选项）
    # 4. 二次启动
    "rally_limit": 0.30,        # 突破后拉升空间上限（≤30%）
    "reconsol_window": 14,      # 二次整理周期上限（≤14 交易日）
    "reconsol_range": 0.10,     # 二次整理振幅上限
    "new_high_window": 120,     # 半年新高窗口
}

# 前向收益统计周期
FWD_DAYS = (5, 10, 20, 60)


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------
def load_stock_panel(start, end, codes=None):
    """加载 astock 日线并复权（hfq），返回 index=(trade_date, ts_code) 的 DataFrame。"""
    t0 = time.time()
    # 注意：trade_date/ts_code 是 parquet 的 pandas 元数据索引列，必须包含在
    # 列投影里，否则 pyarrow 不会恢复 MultiIndex。
    df = pq.read_table(ASTOCK_DAILY, columns=NEED_COLS).to_pandas()
    df = df.reset_index()
    df = df.dropna(subset=["trade_date", "ts_code"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    if codes is not None:
        df = df[df["ts_code"].isin(codes)]
    df = df[(df["trade_date"] >= pd.Timestamp(start))
            & (df["trade_date"] <= pd.Timestamp(end))]
    df = df.sort_values(["ts_code", "trade_date"])
    df = df.set_index(["trade_date", "ts_code"])
    adj = df["adj_factor"].astype(float)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float) * adj
    df["is_st"] = df["is_st"].fillna(0).astype(int)
    df["turnover_rate"] = df["turnover_rate"].astype(float)
    df["circ_mv"] = df["circ_mv"].astype(float)
    print("[load] %d rows, %d codes, %.1fs"
          % (len(df), df.index.get_level_values("ts_code").nunique(),
             time.time() - t0))
    return df


def load_index(start, end):
    """加载上证指数收盘（超额基准）。"""
    import duckdb
    con = duckdb.connect(INDEX_DB, read_only=True)
    rows = con.execute(
        "SELECT trade_date, close FROM index_daily WHERE code=? AND trade_date BETWEEN ? AND ?",
        [INDEX_CODE, start, end]).fetchall()
    con.close()
    s = pd.Series({pd.Timestamp(d): float(c) for d, c in rows})
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


# ---------------------------------------------------------------------------
# 分组向量化工具
# ---------------------------------------------------------------------------
def gshift(s, n):
    """按股票分组的 shift（MultiIndex date,code 必须分组，否则跨股票）。"""
    return s.groupby(level="ts_code").shift(n)


def _roll(series, window, kind):
    tmp = pd.DataFrame({"v": series, "code": series.index.get_level_values("ts_code")})
    if kind == "mean":
        return tmp.groupby("code")["v"].transform(
            lambda x: x.rolling(window, min_periods=window).mean())
    return tmp.groupby("code")["v"].transform(
        lambda x: x.rolling(window, min_periods=window).sum())


def _groll_max(df, col, n):
    return df[col].groupby(level="ts_code").transform(
        lambda x, n=n: x.rolling(n, min_periods=n).max())


def _groll_min(df, col, n):
    return df[col].groupby(level="ts_code").transform(
        lambda x, n=n: x.rolling(n, min_periods=n).min())


def _days_since(bool_series):
    """BARSLAST：距最近一次 True 的天数（无历史为 NaN）。"""
    tmp = pd.DataFrame({"b": bool_series.astype(int),
                        "code": bool_series.index.get_level_values("ts_code")})
    cum = tmp.groupby("code")["b"].transform(lambda x: x.cumsum())
    inner = tmp.groupby("code")["b"].transform(
        lambda x: x.groupby((x == 1).cumsum()).cumcount())
    out = inner.astype(float)
    out = out.where(cum > 0)
    return out


# ---------------------------------------------------------------------------
# 通用指标（向量化，按股票分组）
# ---------------------------------------------------------------------------
def compute_indicators(df):
    """全市场指标（源自 Project_12 huangshi_formula_scan.compute_indicators，
    补齐 hhv/llv 120 与 hhv_c 3/14 供箱体/二次启动使用）。"""
    t0 = time.time()
    g = df.groupby(level="ts_code")
    out = pd.DataFrame(index=df.index)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    vol = df["vol"].astype(float)
    out["close"] = close
    out["open"] = open_
    out["high"] = high
    out["low"] = low
    out["vol"] = vol

    for n in [5, 10, 20, 60, 120]:
        out["ma%d" % n] = g["close"].transform(
            lambda x, n=n: x.rolling(n, min_periods=n).mean())

    ma5 = out["ma5"]
    out["angle5"] = np.arctan((ma5 / gshift(ma5, 1) - 1.0) * 100.0) * 180.0 / np.pi
    out["bias60"] = (close / out["ma60"] - 1.0) * 100.0

    yang = (close > open_).astype(float)
    out["yang_ratio"] = _roll(yang, 10, "mean")
    eff_yang = (close > gshift(close, 1) * 1.005).astype(float)
    out["eff_yang_ratio"] = _roll(eff_yang, 10, "mean")

    out["turnover"] = df["turnover_rate"]
    out["circ_mv"] = df["circ_mv"]
    out["is_st"] = df["is_st"]

    ma_vol5 = g["vol"].transform(lambda x: x.rolling(5, min_periods=5).mean())
    out["vol_ma5"] = ma_vol5
    out["vol_ratio"] = vol / gshift(ma_vol5, 1)          # 当日量 / 前5日均量
    out["vol_surge"] = vol / ma_vol5

    def _ema(ser, span):
        return ser.ewm(span=span, adjust=False).mean()
    ema12 = g["close"].transform(lambda x: _ema(x, 12))
    ema26 = g["close"].transform(lambda x: _ema(x, 26))
    dif = ema12 - ema26
    dea = dif.groupby(level="ts_code").transform(lambda x: _ema(x, 9))
    out["dif"] = dif
    out["dea"] = dea
    out["macd"] = (dif - dea) * 2.0
    out["macd_peak5"] = out["macd"].groupby(level="ts_code").transform(
        lambda x: x.rolling(5, min_periods=5).max())

    typ = (high + low + close) / 3.0
    ma_typ14 = typ.groupby(level="ts_code").transform(
        lambda x: x.rolling(14, min_periods=14).mean())
    avedev14 = typ.groupby(level="ts_code").transform(
        lambda x: x.rolling(14, min_periods=14).apply(
            lambda w: np.abs(w - w.mean()).mean(), raw=True))
    out["cci14"] = (typ - ma_typ14) / (0.015 * avedev14)

    for n in [3, 10, 18, 20, 60, 120]:
        out["hhv_h_%d" % n] = g["high"].transform(
            lambda x, n=n: x.rolling(n, min_periods=n).max())
        out["llv_l_%d" % n] = g["low"].transform(
            lambda x, n=n: x.rolling(n, min_periods=n).min())
    out["hhv_c_3"] = g["close"].transform(
        lambda x: x.rolling(3, min_periods=3).max())
    out["range10"] = (out["hhv_h_10"] / out["llv_l_10"] - 1.0) * 100.0
    out["range20"] = (out["hhv_h_20"] / out["llv_l_20"] - 1.0) * 100.0

    chg = close / gshift(close, 1) - 1.0
    small = (chg.abs() < 0.03).astype(float)
    out["small_chg_5d"] = _roll(small, 5, "sum")

    big_yin = (close < open_ * 0.96) & (vol >= out["vol_ma5"] * 1.5)
    big_yin_high = high.where(big_yin)
    out["last_big_yin_high"] = big_yin_high.groupby(level="ts_code").ffill()
    out["big_yin_days_ago"] = _days_since(big_yin)

    out["far5"] = close / ma5 - 1.0
    out["slope5"] = (ma5 / gshift(ma5, 5) - 1.0) / 5.0 * 100.0

    print("[ind] %.1fs" % (time.time() - t0))
    return out


# ---------------------------------------------------------------------------
# 通用过滤
# ---------------------------------------------------------------------------
def _common_filter(ind, cfg):
    s = pd.Series(True, index=ind.index)
    min_circ = cfg.get("min_circ_mv", 0)
    if min_circ and min_circ > 0:
        s = s & (ind["circ_mv"] >= min_circ)
    if not cfg.get("is_st_ok", 0):
        s = s & (ind["is_st"] == 0)
    return s


# ---------------------------------------------------------------------------
# 1、箱体突破买点
# ---------------------------------------------------------------------------
def signal_box_breakout(ind, cfg):
    box_period = int(cfg.get("box_period", 40))
    box_max_range = float(cfg.get("box_max_range", 0.22))
    low_pos_max = float(cfg.get("low_pos_max", 0.45))
    strong_gain = float(cfg.get("strong_gain", 0.03))
    vol_ratio_min = float(cfg.get("vol_ratio_min", 1.3))

    box_top = _groll_max(ind, "high", box_period)
    box_bottom = _groll_min(ind, "low", box_period)
    box_top_prev = gshift(box_top, 1)
    box_bottom_prev = gshift(box_bottom, 1)

    box_range = box_top_prev / box_bottom_prev - 1.0          # 横盘振幅
    pos120 = ((ind["close"] - ind["llv_l_120"])
              / (ind["hhv_h_120"] - ind["llv_l_120"] + 1e-9))  # 120日区间位置
    gain = ind["close"] / gshift(ind["close"], 1) - 1.0

    s = (box_range <= box_max_range) & (pos120 <= low_pos_max)
    s = s & (ind["close"] > box_top_prev) & (ind["close"] > ind["open"])  # 突破
    s = s & (gain >= strong_gain) & (ind["vol_ratio"] >= vol_ratio_min)   # 强势+放量
    return s & _common_filter(ind, cfg)


# ---------------------------------------------------------------------------
# 2、杯柄图形买点（规则化近似）
# ---------------------------------------------------------------------------
def signal_cup_handle(ind, cfg):
    lookback = int(cfg.get("cup_lookback", 60))
    cup_depth_min = float(cfg.get("cup_depth_min", 0.12))
    cup_depth_max = float(cfg.get("cup_depth_max", 0.38))
    recover_ratio = float(cfg.get("recover_ratio", 0.95))
    handle_window = int(cfg.get("handle_window", 8))
    handle_range = float(cfg.get("handle_range", 0.10))
    vol_ratio_min = float(cfg.get("vol_ratio_min", 1.2))

    rim_high_prev = gshift(_groll_max(ind, "high", lookback), 1)   # 杯沿（不含当日）
    cup_bottom = _groll_min(ind, "low", lookback)                  # 坑底
    depth = (rim_high_prev - cup_bottom) / rim_high_prev           # 挖坑深度

    handle_hhv_prev = gshift(_groll_max(ind, "high", handle_window), 1)
    handle_llv_prev = gshift(_groll_min(ind, "low", handle_window), 1)

    s = (depth >= cup_depth_min) & (depth <= cup_depth_max)        # 有坑
    s = s & (ind["close"] >= rim_high_prev * recover_ratio)        # 回到坑沿
    s = s & ((handle_hhv_prev / handle_llv_prev - 1.0) <= handle_range)  # 杯柄整理
    s = s & (ind["close"] > handle_hhv_prev)                       # 突破杯柄
    s = s & (ind["close"] > ind["open"]) & (ind["vol_ratio"] >= vol_ratio_min)
    return s & _common_filter(ind, cfg)


# ---------------------------------------------------------------------------
# 3、上升趋势回调低吸买点
# ---------------------------------------------------------------------------
def signal_trend_pullback(ind, cfg):
    pullback_max = float(cfg.get("pullback_max", 0.15))
    pullback_min = float(cfg.get("pullback_min", 0.03))
    hhv_ref = int(cfg.get("hhv_ref", 20))
    small_k_days = int(cfg.get("small_k_days", 3))
    vol_low_ratio = float(cfg.get("vol_low_ratio", 0.90))
    macd_enabled = int(cfg.get("macd_enabled", 0))

    hhv_key = "hhv_h_%d" % hhv_ref
    if hhv_key not in ind:
        ind[hhv_key] = _groll_max(ind, "high", hhv_ref)
    recent_high = gshift(ind[hhv_key], 1)
    pullback = (recent_high - ind["close"]) / recent_high

    trend = (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"])
    trend = trend & (ind["ma20"] > ind["ma60"]) & (ind["ma60"] >= gshift(ind["ma60"], 5))

    llv_20_prev = gshift(ind["llv_l_20"], small_k_days)
    no_new_low = _groll_min(ind, "low", small_k_days) >= llv_20_prev  # 近N日不创新低
    small_k = ind["small_chg_5d"] >= (small_k_days - 1)               # 小K线占比

    strengthen = (ind["close"] > ind["open"]) | (ind["close"] > ind["ma5"])

    s = trend & (pullback >= pullback_min) & (pullback <= pullback_max)
    s = s & no_new_low & small_k & (ind["vol_ratio"] <= vol_low_ratio) & strengthen
    if macd_enabled:
        s = s & (ind["dif"] > ind["dea"])
    return s & _common_filter(ind, cfg)


# ---------------------------------------------------------------------------
# 4、二次启动买点（规则化近似）
# ---------------------------------------------------------------------------
def signal_second_launch(ind, cfg):
    box_period = int(cfg.get("box_period", 40))
    rally_limit = float(cfg.get("rally_limit", 0.30))
    reconsol_window = int(cfg.get("reconsol_window", 14))
    reconsol_range = float(cfg.get("reconsol_range", 0.10))
    vol_ratio_min = float(cfg.get("vol_ratio_min", 1.2))
    new_high_window = int(cfg.get("new_high_window", 120))

    box_top = gshift(_groll_max(ind, "high", box_period), 1)   # 底部箱体上沿
    box_bottom = gshift(_groll_min(ind, "low", box_period), 1)
    box_range = box_top / box_bottom - 1.0

    hhv_prev = gshift(_groll_max(ind, "high", new_high_window), reconsol_window)  # 前N日最高（排除二次整理窗口）
    consol_high_prev = gshift(_groll_max(ind, "high", reconsol_window), 1)
    consol_low = _groll_min(ind, "low", reconsol_window)

    s = (box_range <= 0.22)                                        # 底部有箱体
    s = s & (consol_high_prev / box_top - 1.0 <= rally_limit)      # 拉升≤30%
    s = s & (consol_high_prev > hhv_prev)                          # 二次整理期曾创半年新高
    s = s & (consol_high_prev / consol_low - 1.0 <= reconsol_range)  # 二次整理振幅小
    s = s & (ind["ma20"] >= gshift(ind["ma20"], 3))                # 重心不降
    s = s & (ind["close"] > consol_high_prev)                      # 再次启动突破
    s = s & (ind["close"] > ind["open"]) & (ind["vol_ratio"] >= vol_ratio_min)
    return s & _common_filter(ind, cfg)


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
def compute_stats(signal, close_panel, index_close, fwd_days_list=FWD_DAYS):
    sig = signal[signal]
    if len(sig) == 0:
        return pd.DataFrame()
    df_out = pd.DataFrame({
        "date": sig.index.get_level_values("trade_date"),
        "code": sig.index.get_level_values("ts_code"),
    }).set_index(["date", "code"])
    close = close_panel["close"]
    for n in fwd_days_list:
        fwd_close = close.groupby(level="ts_code").shift(-n)
        df_out["fwd%d" % n] = (fwd_close / close - 1.0).reindex(df_out.index) * 100.0
        idx_ret = (index_close.shift(-n) / index_close - 1.0) * 100.0
        idx_map = idx_ret.reindex(df_out.index.get_level_values("date")).values
        df_out["ex%d" % n] = df_out["fwd%d" % n] - idx_map
    return df_out


def summarize(name, df_out):
    print("\n" + "=" * 72)
    print("【%s】 信号总数: %d" % (name, len(df_out)))
    if len(df_out) == 0:
        return None
    n_dates = df_out.index.get_level_values("date").nunique()
    n_codes = df_out.index.get_level_values("code").nunique()
    print("  交易日: %d, 股票: %d, 日均信号: %.1f"
          % (n_dates, n_codes, len(df_out) / n_dates))
    out = {"module": name, "n_signals": int(len(df_out)),
           "n_dates": int(n_dates), "n_codes": int(n_codes),
           "avg_per_day": round(len(df_out) / n_dates, 2)}
    for n in FWD_DAYS:
        col = "fwd%d" % n
        v = df_out[col].dropna()
        if len(v) == 0:
            continue
        hit = (v > 0).mean() * 100.0
        big_thr = 10.0 if n <= 10 else 20.0
        big = (v >= big_thr).mean() * 100.0
        ex = df_out["ex%d" % n].dropna().mean()
        print("  fwd%-3d 均值%+6.2f%% 中位%+6.2f%% 命中率%5.1f%% 大涨>=%.0f%% %4.1f%% 超额%+5.2f%% (n=%d)"
              % (n, v.mean(), v.median(), hit, big_thr, big, ex, len(v)))
        out["fwd%d_mean" % n] = round(float(v.mean()), 2)
        out["fwd%d_median" % n] = round(float(v.median()), 2)
        out["fwd%d_hit" % n] = round(float(hit), 1)
        out["fwd%d_big" % n] = round(float(big), 1)
        out["fwd%d_excess" % n] = round(float(ex), 2)
    if "fwd20" in df_out.columns:
        df_out["year"] = df_out.index.get_level_values("date").year
        y = df_out.groupby("year")["fwd20"].agg(
            ["count", "mean", lambda x: (x > 0).mean() * 100.0]).round(2)
        y.columns = ["n", "fwd20_mean", "hit20"]
        print("  年度 fwd20:")
        print(y.to_string())
        out["yearly"] = {str(k): {"n": int(v["n"]),
                                  "mean": float(v["fwd20_mean"]),
                                  "hit": float(v["hit20"])}
                         for k, v in y.iterrows()}
    return out


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_scan(start, end, cfg=None, quick=False):
    cfg = dict(_DEFAULT_CFG)
    cfg.update(cfg or {})
    print("=== 买卖点信号扫描: %s ~ %s ===" % (start, end))
    df = load_stock_panel(start, end)
    if quick:
        codes = df.index.get_level_values("ts_code").unique()[:400]
        df = df[df.index.get_level_values("ts_code").isin(codes)]
        print("[quick] subset to %d codes" % len(codes))
    idx = load_index(start, end)
    print("[index] %s: %d rows, %s ~ %s"
          % (INDEX_CODE, len(idx), idx.index[0].date(), idx.index[-1].date()))

    ind = compute_indicators(df)
    idx_close = idx.reindex(ind.index.get_level_values("trade_date").unique()).ffill()

    sigs = {
        "1_箱体突破": signal_box_breakout(ind, cfg),
        "2_杯柄图形": signal_cup_handle(ind, cfg),
        "3_趋势低吸": signal_trend_pullback(ind, cfg),
        "4_二次启动": signal_second_launch(ind, cfg),
    }
    for k, v in sigs.items():
        print("  %s: %d 信号" % (k, int(v.sum())))

    results = []
    for name, sig in sigs.items():
        stats_df = compute_stats(sig, df, idx_close)
        r = summarize(name, stats_df)
        if r:
            results.append(r)
    return results, ind, sigs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--quick", action="store_true", help="小样本快速验证（前400只）")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()
    results, _, _ = run_scan(args.start, args.end, quick=args.quick)
    if args.out and results:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print("\n[out] %s" % args.out)
    return results


if __name__ == "__main__":
    main()
