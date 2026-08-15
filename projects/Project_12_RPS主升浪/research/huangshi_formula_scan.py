# coding=utf-8
"""黄氏主升浪四版通达信公式 -> Python PIT 信号扫描与命中率对比。

四版公式（源：F:/天翼云盘同步盘/Obsidian/量化知识库/20_策略知识库/黄氏策略/）：
  1. 双中军版      gs_1_双中军.txt
  2. 匹配主图逻辑版 gs_1_匹配主图逻辑.txt（买点1 OR 买点2，不含 FILTER 去重）
  3. 529版         gs_1_529选股.txt（筹码密集启动突破-实战放宽版）
  4. DeepSeek版    gs_1_deepseek.txt

评估口径（PIT 安全）：
  - 价格用后复权(hfq)连续价计算信号与收益（通达信默认前复权显示，两者形态等价）
  - 信号日收盘产生信号 -> 以当日收盘价买入，持有 N 交易日（5/10/20/60）
  - 大盘(INDEXC)用上证指数 000001.SH（benchmark_index.duckdb）
  - 筹码分布用近似模型：120日窗口，逐日 [low,high] 均匀分布 × 换手率权重
  - 仅对"先满足非筹码条件"的候选日计算筹码，控制计算量

用法：
  python projects/Project_12_RPS主升浪/research/huangshi_formula_scan.py [--start 2019-01-01] [--end 2025-12-31] [--quick]
"""
import sys
import time
import argparse
from collections import defaultdict

sys.path.insert(0, "D:/QuantLab")

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ASTOCK_DAILY = "E:/astock/daily/stock_daily.parquet"
INDEX_DB = "F:/backtest_workspace/data/duckdb/benchmark_index.duckdb"
INDEX_CODE = "000001.SH"  # 上证指数

NEED_COLS = [
    "trade_date", "ts_code", "open", "high", "low", "close",
    "vol", "amount", "adj_factor", "turnover_rate", "circ_mv", "is_st",
]


def gshift(s, n):
    """按股票分组的 shift（MultiIndex: date, code 下必须分组，否则跨股票）。"""
    return s.groupby(level="ts_code").shift(n)


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------
def load_stock_panel(start, end, codes=None):
    t0 = time.time()
    table = pd.read_parquet(ASTOCK_DAILY, columns=[c for c in NEED_COLS if c != "trade_date" and c != "ts_code"])
    df = table
    df = df.reset_index()
    if "trade_date" not in df.columns or "ts_code" not in df.columns:
        raise KeyError("parquet index must be (trade_date, ts_code), got: %s" % df.index.names)
    df = df.dropna(subset=["trade_date", "ts_code"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["ts_code"] = df["ts_code"].astype(str)
    if codes is not None:
        df = df[df["ts_code"].isin(codes)]
    df = df[(df["trade_date"] >= pd.Timestamp(start)) & (df["trade_date"] <= pd.Timestamp(end))]
    df = df.sort_values(["ts_code", "trade_date"])
    df = df.set_index(["trade_date", "ts_code"])
    adj = df["adj_factor"].astype(float)
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float) * adj
    df["is_st"] = df["is_st"].fillna(0).astype(int)
    df["turnover_rate"] = df["turnover_rate"].astype(float)
    df["circ_mv"] = df["circ_mv"].astype(float)
    print("[load] %d rows, %d codes, %.1fs" % (len(df), df.index.get_level_values("ts_code").nunique(), time.time() - t0))
    return df


def load_index(start, end):
    import duckdb
    con = duckdb.connect(INDEX_DB, read_only=True)
    rows = con.execute(
        "SELECT trade_date, close FROM index_daily WHERE code=? AND trade_date BETWEEN ? AND ?",
        [INDEX_CODE, start, end],
    ).fetchall()
    con.close()
    s = pd.Series({pd.Timestamp(d): float(c) for d, c in rows})
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


# --------------------------------------------------------------------------
# 通用指标（按股票分组向量化）
# --------------------------------------------------------------------------
def compute_indicators(df):
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

    # 均线
    for n in [5, 10, 20, 60, 120]:
        out["ma%d" % n] = g["close"].transform(lambda x: x.rolling(n, min_periods=n).mean())

    # MA5 角度（通达信：ATAN((MA5/REF(MA5,1)-1)*100)*180/pi；45°≈日涨1%）
    ma5 = out["ma5"]
    out["angle5"] = np.arctan((ma5 / gshift(ma5, 1) - 1.0) * 100.0) * 180.0 / np.pi

    out["bias60"] = (close / out["ma60"] - 1.0) * 100.0

    # 阳线比例（10日）
    yang = (close > open_).astype(float)
    out["yang_ratio"] = _roll(yang, 10, "mean")

    # 有效阳线（涨幅>0.5%）比例
    eff_yang = (close > gshift(close, 1) * 1.005).astype(float)
    out["eff_yang_ratio"] = _roll(eff_yang, 10, "mean")

    # 换手率 / 流通市值
    out["turnover"] = df["turnover_rate"]
    out["circ_mv"] = df["circ_mv"]

    # 量能：5日均量、量比(当日量/前5日均量)、放量倍数(当日量/5日均量)
    ma_vol5 = g["vol"].transform(lambda x: x.rolling(5, min_periods=5).mean())
    out["vol_ma5"] = ma_vol5
    out["vol_ratio"] = vol / gshift(ma_vol5, 1)
    out["vol_surge"] = vol / ma_vol5

    # MACD
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

    # CCI14（AVEDEV = 平均绝对偏差）
    typ = (high + low + close) / 3.0
    ma_typ14 = typ.groupby(level="ts_code").transform(lambda x: x.rolling(14, min_periods=14).mean())
    avedev14 = typ.groupby(level="ts_code").transform(
        lambda x: x.rolling(14, min_periods=14).apply(lambda w: np.abs(w - w.mean()).mean(), raw=True))
    out["cci14"] = (typ - ma_typ14) / (0.015 * avedev14)

    # 高低点 / 区间
    for n in [3, 10, 18, 20, 60]:
        out["hhv_h_%d" % n] = g["high"].transform(lambda x, n=n: x.rolling(n, min_periods=n).max())
        out["llv_l_%d" % n] = g["low"].transform(lambda x, n=n: x.rolling(n, min_periods=n).min())
    out["hhv_c_3"] = g["close"].transform(lambda x: x.rolling(3, min_periods=3).max())
    out["range10"] = (out["hhv_h_10"] / out["llv_l_10"] - 1.0) * 100.0
    out["range20"] = (out["hhv_h_20"] / out["llv_l_20"] - 1.0) * 100.0

    # 近5日小波动（|涨跌幅|<3%）天数
    chg = close / gshift(close, 1) - 1.0
    small = (chg.abs() < 0.03).astype(float)
    out["small_chg_5d"] = _roll(small, 5, "sum")

    # 大阴线（C<O*0.96 且 V>=5日均量*1.5）及最近大阴线高点、距今天数
    big_yin = (close < open_ * 0.96) & (vol >= out["vol_ma5"] * 1.5)
    big_yin_high = high.where(big_yin)
    out["last_big_yin_high"] = big_yin_high.groupby(level="ts_code").ffill()
    out["big_yin_days_ago"] = _days_since(big_yin)

    # 远离5日线 / 斜率5（5日线5日平均涨幅%）
    out["far5"] = close / ma5 - 1.0
    out["slope5"] = (ma5 / gshift(ma5, 5) - 1.0) / 5.0 * 100.0

    print("[ind] %.1fs" % (time.time() - t0))
    return out


def _roll(series, window, kind):
    tmp = pd.DataFrame({"v": series, "code": series.index.get_level_values("ts_code")})
    if kind == "mean":
        return tmp.groupby("code")["v"].transform(lambda x: x.rolling(window, min_periods=window).mean())
    return tmp.groupby("code")["v"].transform(lambda x: x.rolling(window, min_periods=window).sum())


def _days_since(bool_series):
    """BARSLAST：距最近一次 True 的天数（无历史为 NaN）。"""
    tmp = pd.DataFrame({"b": bool_series.astype(int), "code": bool_series.index.get_level_values("ts_code")})
    cum = tmp.groupby("code")["b"].transform(lambda x: x.cumsum())
    out = pd.Series(np.nan, index=bool_series.index)
    # 每个 True 之后重新计数
    inner = tmp.groupby("code")["b"].transform(lambda x: x.groupby((x == 1).cumsum()).cumcount())
    out = inner.astype(float)
    out = out.where(cum > 0)
    return out


# --------------------------------------------------------------------------
# 四版公式信号（均为向量化布尔 Series）
# --------------------------------------------------------------------------
def signal_shuangzhongjun(ind, idx_gt):
    """双中军：五线多头(5>10>20>60>120)+发散(角度30+MA5/MA20>1.05)+MACD零上+CCI100+
    突破20日高(<8%)+MA20/60向上+大盘 INDEXC>MA20>MA60。"""
    s = (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"])
    s = s & (ind["ma20"] > ind["ma60"]) & (ind["ma60"] > ind["ma120"])
    s = s & (ind["angle5"] > 30.0) & (ind["ma5"] / ind["ma20"] > 1.05)
    cross_up = (ind["dif"] > ind["dea"]) & (gshift(ind["dif"], 1) <= gshift(ind["dea"], 1))
    macd_ok = (cross_up & (ind["dea"] > 0)) | (
        (ind["dif"] > ind["dea"]) & (ind["dif"] > gshift(ind["dif"], 1)) & (ind["dea"] > gshift(ind["dea"], 1)))
    s = s & macd_ok
    cci_cross = (ind["cci14"] > 100.0) & (gshift(ind["cci14"], 1) <= 100.0)
    cci_ok = cci_cross | ((ind["cci14"] > 100.0) & (ind["cci14"] > gshift(ind["cci14"], 1)))
    s = s & cci_ok
    hhv20_prev = gshift(ind["hhv_h_20"], 1)
    s = s & (ind["close"] > hhv20_prev) & (ind["close"] / hhv20_prev < 1.08)
    s = s & (ind["ma20"] > gshift(ind["ma20"], 5)) & (ind["ma60"] > gshift(ind["ma60"], 5))
    s = s & idx_gt
    return s


def signal_match_master(ind, idx_ret, idx_daily_gt):
    """匹配主图逻辑版：买点1 OR 买点2（不含 FILTER 去重）。
    共性否定过滤：横盘/不强于板块/MACD峰位/追高/追阴/箱体震荡。"""
    # 个股当日涨幅 vs 板块涨幅
    stock_ret = ind["close"] / gshift(ind["close"], 1) - 1.0
    stronger = stock_ret > idx_ret

    # MACD 峰位禁止买
    macd_peak = ind["macd_peak5"]
    at_right = (ind["macd"] < gshift(macd_peak, 1)) & (gshift(ind["macd"], 1) == gshift(macd_peak, 1))
    at_left = (ind["macd"] > gshift(ind["macd"], 1)) & (ind["macd"] < macd_peak)
    forbid_macd = ((ind["dif"] > 0) & at_right) | ((ind["dif"] < 0) & at_left)

    # 追高：远离5日>8% 且 放量>=2.5
    forbid_chase = (ind["far5"] > 0.08) & (ind["vol_surge"] >= 2.5)

    # 追阴：近10日有大阴线 且 C<大阴高点（从未出现大阴线则不禁止）
    forbid_yin = (ind["big_yin_days_ago"] <= 10) & (ind["close"] < ind["last_big_yin_high"])

    # 箱体震荡：20日振幅>=8% 且 C 在箱内
    box = (ind["range20"] >= 8.0) & (ind["close"] < ind["hhv_h_20"]) & (ind["close"] > ind["llv_l_20"])

    common = (ind["range10"] > 10.0) & stronger & ~forbid_macd & ~forbid_chase & ~forbid_yin & ~box

    # 买点1：强势启动
    b1 = (ind["close"] > ind["ma5"]) & (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"]) & (ind["ma20"] > ind["ma60"])
    b1 = b1 & (ind["close"] > ind["open"]) & (ind["low"] >= ind["ma5"] * 0.98)
    b1 = b1 & (ind["angle5"] >= 45.0) & (ind["yang_ratio"] >= 0.6) & common

    # 买点2：多头趋势回踩
    b2 = (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"]) & (ind["ma20"] > ind["ma60"])
    b2 = b2 & (ind["low"] >= ind["ma5"] * 0.98) & (ind["close"] >= ind["ma5"])
    b2 = b2 & (ind["close"] > ind["open"]) & (ind["hhv_h_3"] / ind["low"] <= 1.10) & common
    return b1 | b2


def pre_signal_529(ind):
    """529 非筹码部分（供筹码计算筛候选）。"""
    hhv60_prev = gshift(ind["hhv_h_60"], 1)
    c_gt = ind["close"] > hhv60_prev
    c_prev_lt = gshift(ind["close"], 1) <= hhv60_prev
    b_any = c_gt & c_prev_lt
    b_any = b_any | (c_gt & (gshift(ind["close"], 1) > hhv60_prev) & (gshift(ind["close"], 2) <= hhv60_prev))
    b_any = b_any | (c_gt & (gshift(ind["close"], 1) > hhv60_prev) & (gshift(ind["close"], 2) > hhv60_prev) & (gshift(ind["close"], 3) <= hhv60_prev))
    s = b_any & (ind["small_chg_5d"] >= 2.0)
    s = s & (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"]) & (ind["ma60"] >= gshift(ind["ma60"], 1))
    s = s & (ind["angle5"] >= 30.0) & (ind["close"] > ind["open"])
    pit = (ind["hhv_h_18"] - ind["llv_l_18"]) / ind["hhv_h_18"] * 100.0 >= 16.0
    ji_la = ind["hhv_c_3"] / ind["llv_l_18"] >= 1.13
    s = s & ~(pit & ji_la)
    return s


def signal_529(ind, cost5, cost95):
    chip = (cost95 - cost5) / cost5 * 100.0 <= 25.0
    return pre_signal_529(ind) & chip


def signal_deepseek(ind, idx_daily_gt):
    """DeepSeek：归一化斜率+乖离60<30+有效阳线50%+大盘+换手3~10+量比1.2~4+流通50~500亿。"""
    s = (ind["close"] > ind["ma5"]) & (ind["ma5"] > ind["ma10"]) & (ind["ma10"] > ind["ma20"]) & (ind["ma20"] > ind["ma60"])
    s = s & (ind["close"] > ind["open"]) & (ind["low"] >= ind["ma5"] * 0.98)
    s = s & (ind["slope5"] >= 2.5) & (ind["yang_ratio"] >= 0.6)
    s = s & (ind["bias60"] < 30.0) & (ind["eff_yang_ratio"] >= 0.5)
    s = s & idx_daily_gt
    s = s & (ind["turnover"] > 3.0) & (ind["turnover"] < 10.0)
    s = s & (ind["vol_ratio"] > 1.2) & (ind["vol_ratio"] < 4.0)
    s = s & (ind["circ_mv"] >= 500000.0) & (ind["circ_mv"] < 5000000.0)
    return s


# --------------------------------------------------------------------------
# 筹码分布近似（仅对候选日）
# --------------------------------------------------------------------------
def compute_cost_candidates(df, pre529):
    t0 = time.time()
    cand = pre529[pre529].index
    if len(cand) == 0:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    code_dates = defaultdict(list)
    for dt, code in cand:
        code_dates[code].append(dt)
    print("  [cost] %d candidates across %d codes" % (len(cand), len(code_dates)))
    cost5, cost95 = [], []
    n = 0
    for code, dates in code_dates.items():
        try:
            hist = df.xs(code, level="ts_code")[["low", "high", "turnover_rate"]].astype(float)
        except KeyError:
            continue
        hist_idx = hist.index
        for dt in dates:
            pos = hist_idx.searchsorted(dt, side="right") - 1
            if pos < 119:
                continue
            win = hist.iloc[pos - 119:pos + 1]
            lo = win["low"].values
            hi = win["high"].values
            w = np.maximum(win["turnover_rate"].values, 0.05)
            lo = np.minimum(lo, hi)
            hi = np.maximum(lo, hi)
            segs = 8
            price_pts = np.empty(len(lo) * segs)
            w_pts = np.empty(len(lo) * segs)
            for i in range(len(lo)):
                grid = np.linspace(lo[i], hi[i], segs + 1)
                price_pts[i * segs:(i + 1) * segs] = (grid[:-1] + grid[1:]) / 2.0
                w_pts[i * segs:(i + 1) * segs] = w[i] / segs
            order = np.argsort(price_pts)
            ps, ws = price_pts[order], w_pts[order]
            cum = np.cumsum(ws)
            if cum[-1] <= 0:
                continue
            cum = cum / cum[-1]
            cost5.append((dt, code, float(ps[np.searchsorted(cum, 0.05)])))
            cost95.append((dt, code, float(ps[np.searchsorted(cum, 0.95)])))
            n += 1
            if n % 50000 == 0:
                print("    %d done, %.0fs" % (n, time.time() - t0))
    print("  [cost] %d computed in %.1fs" % (n, time.time() - t0))
    s5 = pd.Series({(d, c): v for d, c, v in cost5}, dtype=float)
    s95 = pd.Series({(d, c): v for d, c, v in cost95}, dtype=float)
    s5.index = pd.MultiIndex.from_tuples(s5.index, names=["trade_date", "ts_code"])
    s95.index = pd.MultiIndex.from_tuples(s95.index, names=["trade_date", "ts_code"])
    return s5, s95


# --------------------------------------------------------------------------
# 统计
# --------------------------------------------------------------------------
def compute_stats(signal, close_panel, index_close, fwd_days_list=(5, 10, 20, 60)):
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
    print("  交易日: %d, 股票: %d, 日均信号: %.1f" % (n_dates, n_codes, len(df_out) / n_dates))
    out = {"formula": name, "n_signals": len(df_out), "n_dates": n_dates, "n_codes": n_codes}
    for n in [5, 10, 20, 60]:
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
    # 年度分解（fwd20）
    if "fwd20" in df_out.columns:
        df_out["year"] = df_out.index.get_level_values("date").year
        y = df_out.groupby("year")["fwd20"].agg(["count", "mean",
                                                  lambda x: (x > 0).mean() * 100.0]).round(2)
        y.columns = ["n", "fwd20_mean", "hit20"]
        print("  年度 fwd20:")
        print(y.to_string())
        out["yearly"] = {str(k): {"n": int(v["n"]), "mean": float(v["fwd20_mean"]),
                                  "hit": float(v["hit20"])} for k, v in y.iterrows()}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--quick", action="store_true", help="小样本快速验证（前300只）")
    args = ap.parse_args()

    print("=== 黄氏主升浪公式扫描: %s ~ %s ===" % (args.start, args.end))
    df = load_stock_panel(args.start, args.end)
    if args.quick:
        codes = df.index.get_level_values("ts_code").unique()[:300]
        df = df[df.index.get_level_values("ts_code").isin(codes)]
        print("[quick] subset to %d codes" % len(codes))
    idx = load_index(args.start, args.end)
    print("[index] %s: %d rows, %s ~ %s" % (INDEX_CODE, len(idx), idx.index[0].date(), idx.index[-1].date()))

    ind = compute_indicators(df)

    # 大盘对齐
    dates_all = ind.index.get_level_values("trade_date").unique()
    idx_close = idx.reindex(dates_all).ffill()
    idx_ma20 = idx_close.rolling(20, min_periods=20).mean()
    idx_ma60 = idx_close.rolling(60, min_periods=60).mean()
    idx_gt = (idx_close > idx_ma20) & (idx_ma20 > idx_ma60)
    idx_daily_gt = idx_close > idx_ma20
    idx_ret = idx_close / idx_close.shift(1) - 1.0
    idx_gt_s = idx_gt.reindex(ind.index.get_level_values("trade_date")).values
    idx_daily_gt_s = idx_daily_gt.reindex(ind.index.get_level_values("trade_date")).values
    idx_ret_s = idx_ret.reindex(ind.index.get_level_values("trade_date")).values

    print("\n计算四版信号...")
    sigs = {}
    sigs["双中军"] = signal_shuangzhongjun(ind, idx_gt_s)
    sigs["匹配主图"] = signal_match_master(ind, idx_ret_s, idx_daily_gt_s)
    pre529 = pre_signal_529(ind)
    print("  529 非筹码候选: %d" % int(pre529.sum()))
    cost5, cost95 = compute_cost_candidates(df, pre529)
    sigs["529"] = signal_529(ind, cost5, cost95)
    sigs["DeepSeek"] = signal_deepseek(ind, idx_daily_gt_s)
    for k, v in sigs.items():
        print("  %s: %d 信号" % (k, int(v.sum())))

    results = []
    for name, sig in sigs.items():
        stats_df = compute_stats(sig, df, idx_close)
        r = summarize(name, stats_df)
        if r:
            results.append(r)
    return results


if __name__ == "__main__":
    main()