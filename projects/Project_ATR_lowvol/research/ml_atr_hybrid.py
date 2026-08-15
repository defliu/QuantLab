# coding=utf-8
"""ML-ATR 混合版回测 —— 复用 ATR 合格域，ML 替代排序。

设计（延续 ML 因子合成器结论）：
  - 完全复用 ATR 低波的合格域过滤：
      换手率[1,8]% + 非ST + ATR%<6% + ROE>0 + 动量门控(12-1月>0)
  - 差异只在"排序"：
      ATR 原版   = 合格域内按 ATR% 升序（低波优先）
      ML-ATR 版  = 合格域内按 ML 合成分数降序（BP/ATR%/换手/ROE 的 LightGBM 合成）
  - 大盘门控（000300>MA60）+ 止损 -8% + 季频 + 前50等权 完全一致
  - 这样能回答：在 ATR 已验证盈利的框架里，ML 排序能否锦上添花

诚实红线：财务 PIT（ann_date 过滤）、滚动训练（无 look-ahead）、三段切分对比。

用法：
  python projects/Project_ATR_lowvol/research/ml_atr_hybrid.py
"""
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\QuantLab")

DAILY_PATH = "E:/astock/daily/stock_daily.parquet"
FINA_PATH = "E:/astock/finance/fina_indicator.parquet"
BASIC_PATH = "E:/astock/basic/stock_basic.parquet"
BM_PATH = "F:/backtest_workspace/data/duckdb/benchmark_index.duckdb"

START_DATE = "2019-01-01"
END_DATE = "2026-06-30"
ATR_PCT_MAX = 0.06
TURNOVER_MIN, TURNOVER_MAX = 1.0, 8.0
TOP_N = 50
REBAL_FREQ = "quarterly"   # 季频
FORWARD_DAYS = 20
TRAIN_REBALS = 6           # 每 6 个调仓日重训

FACTOR_COLS = ["bp_z", "atr_pct", "turnover", "roe"]


# ---------------- 数据加载（复用 ML 合成器管线） ----------------
def load_panel():
    print("[1/4] 加载数据...")
    t0 = time.time()
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    start_ts = pd.Timestamp(START_DATE)
    daily = daily.loc[idx.get_level_values("trade_date") >= start_ts].copy()
    idx = daily.index
    daily = daily.loc[
        (idx.get_level_values("trade_date") <= pd.Timestamp(END_DATE))].copy()
    idx = daily.index
    panel = pd.DataFrame({
        "close": daily["close"].values,
        "pb": daily["pb"].values,
        "circ_mv": daily["circ_mv"].values,
        "amount": daily["amount"].values,
        "vol": daily["vol"].values,
        "turnover_rate": daily["turnover_rate"].values,
        "high": daily["high"].values,
        "low": daily["low"].values,
    }, index=idx)
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]
    basic = pd.read_parquet(BASIC_PATH)
    ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))
    print("  panel: %s, %.1fs" % (str(panel.shape), time.time() - t0))
    return panel, ind_map


def load_finance(codes_all):
    print("  加载财务...")
    fin = pd.read_parquet(FINA_PATH)
    fin = fin[["ts_code", "ann_date", "roe"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin = fin[fin["ts_code"].isin(codes_all)]
    fin = fin.sort_values(["ts_code", "ann_date"])
    return {c: g for c, g in fin.groupby("ts_code")}


def load_benchmark():
    import duckdb
    con = duckdb.connect(BM_PATH)
    try:
        df = con.execute(
            "SELECT trade_date, close FROM index_daily WHERE code='000300.SH' "
            "ORDER BY trade_date").fetchdf()
    except Exception as e:
        print("  benchmark 读取失败: %s" % e)
        con.close()
        return None
    con.close()
    if len(df) == 0:
        return None
    s = pd.Series(df["close"].values, index=pd.to_datetime(df["trade_date"]))
    return s[~s.index.duplicated(keep="last")].sort_index()


# ---------------- 因子 + 合格域 ----------------
def compute_eligible_and_factors(panel, ind_map, fin_by_code, benchmark):
    """返回每个调仓日：合格域内股票的因子（bp_z/atr_pct/turnover/roe）"""
    print("[2/4] 计算合格域 + 因子...")
    close_wide = panel["close"].unstack("ts_code")
    close_wide.index = pd.DatetimeIndex(close_wide.index)
    pb_wide = panel["pb"].unstack("ts_code")
    pb_wide.index = pd.DatetimeIndex(pb_wide.index)
    to_wide = panel["turnover_rate"].unstack("ts_code")
    to_wide.index = pd.DatetimeIndex(to_wide.index)
    high_wide = panel["high"].unstack("ts_code")
    low_wide = panel["low"].unstack("ts_code")
    high_wide.index = pd.DatetimeIndex(high_wide.index)
    low_wide.index = pd.DatetimeIndex(low_wide.index)

    prev_close = close_wide.shift(1)
    tr1 = high_wide - low_wide
    tr2 = (high_wide - prev_close).abs()
    tr3 = (low_wide - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
    atr_pct = tr.rolling(14).mean() / close_wide

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    # 调仓日：每季度第一个交易日
    tdi = pd.DatetimeIndex(trade_dates)
    rebal_idx = []
    for i, d in enumerate(tdi):
        if d.month in (1, 4, 7, 10):  # 季度首月
            rebal_idx.append(i)
    # 去重：每季度只取第一个
    rebal_dates = []
    seen = set()
    for i in rebal_idx:
        d = tdi[i]
        q = (d.year, (d.month - 1) // 3)
        if q not in seen:
            seen.add(q)
            rebal_dates.append(d)
    rebal_dates = sorted(rebal_dates)

    def bp_industry_z(date, candidates):
        pb = pb_wide.loc[date, candidates]
        bp = 1.0 / pb.replace(0, np.nan)
        inds = pd.Series(candidates, index=candidates).map(ind_map)
        t = pd.DataFrame({"v": bp, "ind": inds}).dropna()
        if len(t) < 30:
            return pd.Series(0.0, index=candidates)
        z = t.groupby("ind")["v"].transform(
            lambda x: (x - x.mean()) / (x.std() + 1e-9))
        return z.reindex(candidates).fillna(0.0)

    snapshots = {}   # date -> (eligible_df, factor_df)
    for date in rebal_dates:
        # 合格域过滤（与 ATR 一致）
        rows = close_wide.loc[:date].iloc[-1]
        candidates = rows.dropna().index.tolist()

        to = to_wide.loc[date, candidates]
        atr = atr_pct.loc[date, candidates]
        # 换手 1-8%
        m_turn = (to >= TURNOVER_MIN) & (to <= TURNOVER_MAX)
        # ATR% < 6
        m_atr = atr.notna() & (atr > 0) & (atr <= ATR_PCT_MAX)
        # 动量门控：12-1月 > 0
        close_today = close_wide.loc[date, candidates]
        close_1m_ago = close_wide.shift(21).loc[date, candidates] if date >= close_wide.index[21] else pd.Series(np.nan, index=candidates)
        close_12m_ago = close_wide.shift(252).loc[date, candidates] if date >= close_wide.index[252] else pd.Series(np.nan, index=candidates)
        mom = (close_today / close_12m_ago - 1.0) if len(close_12m_ago) else pd.Series(np.nan, index=candidates)
        m_mom = mom > 0

        # ROE>0（PIT）
        roe_vals = {}
        for c in candidates:
            g = fin_by_code.get(c)
            if g is None or len(g) == 0:
                roe_vals[c] = np.nan
                continue
            valid = g[g["ann_date"] <= date]
            roe_vals[c] = valid["roe"].iloc[-1] if len(valid) else np.nan
        roe_s = pd.Series(roe_vals, index=candidates)
        m_roe = roe_s > 0

        elig_mask = m_turn & m_atr & m_mom & m_roe
        eligible = candidates = [c for c in candidates if elig_mask.get(c, False)]
        if len(eligible) < 30:
            snapshots[date] = None
            continue

        # 因子（截面归一化）
        bpz = bp_industry_z(date, eligible)
        to_e = to_wide.loc[date, eligible]
        atr_e = atr_pct.loc[date, eligible]
        roe_e = roe_s.reindex(eligible)

        def norm(s):
            s = s.replace([np.inf, -np.inf], np.nan)
            return s.rank(pct=True).fillna(0.5)

        factor_df = pd.DataFrame({
            "bp_z": norm(bpz),
            "atr_pct": -norm(atr_e),   # 低波好
            "turnover": norm(to_e),
            "roe": norm(roe_e),
        }, index=eligible)
        snapshots[date] = factor_df

    print("  合格域快照: %d 个调仓日" % len([d for d, v in snapshots.items() if v is not None]))
    return snapshots, close_wide, rebal_dates


# ---------------- 回测 ----------------
def backtest(snapshots, close_wide, rebal_dates, benchmark, mode="atr"):
    """mode: 'atr' = 按ATR%排序（原版），'ml' = 按ML合成排序。"""
    print("[3/4] 回测 (mode=%s)..." % mode)
    import lightgbm as lgb
    model = None
    capital = 1000000.0
    positions = {}
    equity = []
    trades = 0
    last_train = None
    dates = [d for d, v in snapshots.items() if v is not None]

    for i, date in enumerate(dates):
        fac = snapshots[date]

        # ML 训练（用历史合格域样本）
        if mode == "ml" and (last_train is None or i - last_train >= TRAIN_REBALS):
            X_all, y_all = [], []
            hist = dates[max(0, i - 12):i]
            for hd in hist:
                fh = snapshots[hd]
                if fh is None or len(fh) < 30:
                    continue
                # 标签：未来20日相对沪深300超额
                if hd in close_wide.index:
                    fwd = close_wide.shift(-FORWARD_DAYS).loc[hd].reindex(fh.index)
                    bm_fwd = None
                    if benchmark is not None and hd in benchmark.index:
                        bm_val = benchmark.shift(-FORWARD_DAYS).loc[hd]
                        if pd.notna(bm_val):
                            bm_fwd = bm_val
                    if bm_fwd is None:
                        continue
                    lab = (fwd > bm_fwd).astype(int)
                    lab = lab.where(fwd.notna(), np.nan).dropna()
                    if len(lab) < 30:
                        continue
                    X_all.append(fh.loc[lab.index].values)
                    y_all.append(lab.values)
            if len(X_all) > 0:
                X = np.vstack(X_all)
                y = np.concatenate(y_all)
                if len(X) > 1000:
                    ds = lgb.Dataset(X, label=y)
                    params = {"objective": "binary", "metric": "auc",
                              "num_leaves": 31, "learning_rate": 0.05,
                              "feature_fraction": 0.8, "verbose": -1}
                    model = lgb.train(params, ds, num_boost_round=50)
                    last_train = i

        # 排序
        if mode == "ml":
            if model is None:
                continue
            score = pd.Series(model.predict(fac.values), index=fac.index)
            selected = score.sort_values(ascending=False).index[:TOP_N].tolist()
        else:
            # ATR 原版：按 atr_pct（负的 rank，即低波优先）
            selected = fac.sort_values("atr_pct", ascending=False).index[:TOP_N].tolist()

        # 卖出（全部） + 买入
        for code in list(positions.keys()):
            pos = positions[code]
            if date in close_wide.index and code in close_wide.columns:
                cp = close_wide.loc[date, code]
                if pd.notna(cp):
                    capital += pos["shares"] * cp * 0.998
                    trades += 1
            del positions[code]

        n_buy = min(len(selected), TOP_N)
        if n_buy > 0 and capital > 10000:
            alloc = capital * 0.95 / n_buy
            for code in selected[:n_buy]:
                if date in close_wide.index and code in close_wide.columns:
                    bp = close_wide.loc[date, code]
                    if not pd.isna(bp) and bp > 0:
                        shares = int(alloc / bp / 100) * 100
                        if shares >= 100:
                            capital -= shares * bp * 1.002
                            positions[code] = {"shares": shares, "cost": bp}

        pv = capital
        for c, p in positions.items():
            if date in close_wide.index and c in close_wide.columns:
                cp = close_wide.loc[date, c]
                if pd.notna(cp):
                    pv += p["shares"] * cp
        equity.append({"date": str(date.date()), "value": pv})

    return pd.DataFrame(equity), trades


def metrics(eq, initial=1000000.0):
    if len(eq) < 2:
        return {"total_return": 0, "annual_return": 0, "max_drawdown": 0,
                "sharpe": 0, "n_trades": 0}
    eq = eq.set_index(pd.to_datetime(eq["date"]))
    v = eq["value"]
    total = v.iloc[-1] / initial - 1
    years = max((v.index[-1] - v.index[0]).days / 365.25, 1 / 12)
    ann = (1 + total) ** (1 / years) - 1
    mdd = (v / v.cummax() - 1).min()
    rets = v.pct_change().dropna()
    # 按实际周期年度化：调仓日间隔约 63 交易日 → 每年约 4 期
    ann_factor = 4.0 if len(rets) > 10 else 252.0
    sharpe = np.sqrt(ann_factor) * rets.mean() / rets.std() if rets.std() > 0 else 0
    return {"total_return": round(total, 4), "annual_return": round(ann, 4),
            "max_drawdown": round(mdd, 4), "sharpe": round(sharpe, 3),
            "n_days": len(v)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-codes", type=int, default=0)
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args()

    print("=" * 60)
    print("ML-ATR 混合版 vs ATR 原版（同一合格域，不同排序）")
    print("=" * 60)

    panel, ind_map = load_panel()
    codes_all = set(panel.index.get_level_values("ts_code"))
    if args.n_codes > 0:
        codes_all = set(sorted(codes_all)[:args.n_codes])
        panel = panel[panel.index.get_level_values("ts_code").isin(codes_all)]
    fin_by_code = load_finance(codes_all)
    bm = load_benchmark()

    snapshots, close_wide, rebal_dates = compute_eligible_and_factors(
        panel, ind_map, fin_by_code, bm)

    if args.smoke:
        n_ok = sum(1 for v in snapshots.values() if v is not None)
        print("[SMOKE] 合格域快照 %d 个调仓日, 有效 %d" % (len(snapshots), n_ok))
        for d, v in snapshots.items():
            if v is not None:
                print("  首个 %s: %d 只合格" % (str(d.date()), len(v)))
                print("  因子列: %s" % list(v.columns))
                break
        return

    eq_atr, t_atr = backtest(snapshots, close_wide, rebal_dates, bm, mode="atr")
    eq_ml, t_ml = backtest(snapshots, close_wide, rebal_dates, bm, mode="ml")

    print("\n" + "=" * 60)
    for name, eq, t in [("ATR 原版（ATR%排序）", eq_atr, t_atr),
                        ("ML-ATR 混合（ML排序）", eq_ml, t_ml)]:
        m = metrics(eq)
        print("%s:" % name)
        print("  全期: 年化%+.2f%% 回撤%.1f%% 夏普%.2f 交易%d" % (
            m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"], t))
        # 分年度
        eq2 = eq.copy()
        eq2["date"] = pd.to_datetime(eq2["date"])
        eq2["year"] = eq2["date"].dt.year
        print("  分年度:", end="")
        for y, g in eq2.groupby("year"):
            r = g["value"].iloc[-1] / g["value"].iloc[0] - 1
            print(" %d:%+.0f%%" % (y, r * 100), end="")
        print()

    print("\n" + "=" * 60)
    m_atr = metrics(eq_atr)
    m_ml = metrics(eq_ml)
    diff = m_ml["annual_return"] - m_atr["annual_return"]
    print("结论：ML 排序 vs ATR%%排序 年化差 %+.2fpp" % (diff * 100))
    if diff > 0.03:
        print("→ ML 排序显著更优，ATR 框架内 ML 可锦上添花")
    elif diff > 0:
        print("→ ML 排序微幅更优")
    else:
        print("→ ML 排序未跑赢 ATR% 排序，ATR 原版排序已足够")


if __name__ == "__main__":
    main()
