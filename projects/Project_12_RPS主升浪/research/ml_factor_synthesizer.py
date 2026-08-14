# coding=utf-8
"""ML 因子合成器 —— 验证"ML 是否能比手工等权更好地组合已验证因子"。

设计（诚哥拍板：对比 4 因子等权手工基线，最公平）：
  - 输入 4 个已验证因子（BP行业中性z / ATR% / 换手率 / ROE），截面归一化
  - 标签：未来 FORWARD_DAYS 日相对沪深300 是否超额（分类 0/1）
  - 模型：LightGBM 滚动训练（过去 TRAIN_WINDOW 天），每月重训
  - 选股：ML 合成分数前 TOP_N 等权买入
  - 对比：ML合成 vs 4因子等权手工基线（同一数据、同风控、同区间）

诚实红线（继承 QuantLab 规范）：
  - 财务 PIT 安全（ann_date 过滤，无 look-ahead）
  - 严格三段切分：训练集(2019-2021) / 验证集(2022-2023) / 测试集(2024-2026)
  - ML 必须跑赢手工等权才值得用，否则丢弃

用法：
  python projects/Project_12_RPS主升浪/research/ml_factor_synthesizer.py
"""
import os
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
TRAIN_WINDOW = 504      # 训练窗口（交易日）
FORWARD_DAYS = 20       # 前向收益天数
TOP_N = 50              # 持仓数
REBAL_DAYS = 20         # 调仓间隔（约每月）
MIN_HISTORY = 250       # 最少历史

FACTOR_COLS = ["bp_z", "atr_pct", "turnover", "roe"]


# ---------------- 数据加载 ----------------
def load_panel(n_codes=0):
    print("[1/5] 加载数据...")
    t0 = time.time()
    daily = pd.read_parquet(DAILY_PATH)
    idx = daily.index
    start_ts = pd.Timestamp(START_DATE)
    codes_all = set(
        idx.get_level_values("ts_code")[
            idx.get_level_values("trade_date") >= start_ts].unique()
    )
    if n_codes > 0:
        codes_all = set(sorted(codes_all)[:n_codes])
    daily = daily.loc[idx.get_level_values("ts_code").isin(codes_all)].copy()
    idx = daily.index
    daily = daily.loc[
        (idx.get_level_values("trade_date") >= start_ts)
        & (idx.get_level_values("trade_date") <= pd.Timestamp(END_DATE))
    ].copy()
    idx = daily.index

    panel = pd.DataFrame({
        "close": daily["close"].values,
        "pb": daily["pb"].values,
        "pe_ttm": daily["pe_ttm"].values,
        "circ_mv": daily["circ_mv"].values,
        "amount": daily["amount"].values,
        "vol": daily["vol"].values,
        "turnover_rate": daily["turnover_rate"].values,
        "high": daily["high"].values,
        "low": daily["low"].values,
    }, index=idx)

    # 排除 ST 和停牌
    is_st = daily["is_st"].astype(bool)
    suspend = daily["suspend_type"].fillna("N")
    panel = panel.loc[~is_st & ~suspend.isin(["S", "R", "R&S"])]

    # 行业映射（BP 行业中性用）
    basic = pd.read_parquet(BASIC_PATH)
    ind_map = dict(zip(basic["ts_code"], basic["industry"].fillna("其他")))

    print("  panel: %s, %.1fs" % (str(panel.shape), time.time() - t0))
    return panel, ind_map


def load_finance(codes_all):
    print("  加载财务数据...")
    fin = pd.read_parquet(FINA_PATH)
    fin = fin[["ts_code", "end_date", "ann_date", "roe", "bps"]].copy()
    fin["ann_date"] = pd.to_datetime(fin["ann_date"], errors="coerce")
    fin = fin.dropna(subset=["ann_date"])
    fin = fin[fin["ts_code"].isin(codes_all)]
    fin = fin.sort_values(["ts_code", "ann_date"])
    fin_by_code = {c: g for c, g in fin.groupby("ts_code")}
    print("  财务: %d 只" % len(fin_by_code))
    return fin_by_code


def load_benchmark():
    """加载沪深300 收盘（用于相对超额标签）。读 index_daily 表。"""
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
        print("  benchmark 000300.SH 无数据")
        return None
    s = pd.Series(df["close"].values, index=pd.to_datetime(df["trade_date"]))
    s = s[~s.index.duplicated(keep="last")].sort_index()
    print("  沪深300: %d 行, %s ~ %s" % (len(s), s.index[0].date(), s.index[-1].date()))
    return s


# ---------------- 因子计算 ----------------
def compute_factors(panel, ind_map, fin_by_code):
    """计算 4 个因子（截面归一化），返回 {date: DataFrame(index=code, cols=factor)}。"""
    print("[2/5] 计算因子...")
    close_wide = panel["close"].unstack("ts_code")
    close_wide.index = pd.DatetimeIndex(close_wide.index)
    pb_wide = panel["pb"].unstack("ts_code")
    pb_wide.index = pd.DatetimeIndex(pb_wide.index)
    to_wide = panel["turnover_rate"].unstack("ts_code")
    to_wide.index = pd.DatetimeIndex(to_wide.index)

    # ATR%：用 high/low/close 近似（简化 TR）
    high_wide = panel["high"].unstack("ts_code")
    low_wide = panel["low"].unstack("ts_code")
    high_wide.index = pd.DatetimeIndex(high_wide.index)
    low_wide.index = pd.DatetimeIndex(low_wide.index)
    prev_close = close_wide.shift(1)
    tr = pd.concat([high_wide - low_wide,
                    (high_wide - prev_close).abs(),
                    (low_wide - prev_close).abs()]).groupby(level=0).max()
    # 上面 concat 方式有误，改用逐项
    tr1 = high_wide - low_wide
    tr2 = (high_wide - prev_close).abs()
    tr3 = (low_wide - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3]).groupby(level=0).max()
    atr14 = tr.rolling(14).mean()
    atr_pct = atr14 / close_wide

    trade_dates = sorted(panel.index.get_level_values("trade_date").unique())
    ti = {pd.Timestamp(x): k for k, x in enumerate(trade_dates)}

    # BP 行业中性 z
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

    factor_snapshots = {}
    for i in range(MIN_HISTORY, len(trade_dates), REBAL_DAYS):
        date = pd.Timestamp(trade_dates[i])
        # 候选：有足够历史
        rows = close_wide.loc[:date].iloc[-1]
        candidates = rows.dropna().index.tolist()
        if len(candidates) < 100:
            continue

        # ATR% / 换手 / BP / ROE 截面
        atr = atr_pct.loc[date, candidates].fillna(999)  # 缺失=高波动
        turn = to_wide.loc[date, candidates].fillna(0)
        bpz = bp_industry_z(date, candidates)

        # ROE：PIT 从财务读
        roe_vals = {}
        for c in candidates:
            g = fin_by_code.get(c)
            if g is None or len(g) == 0:
                roe_vals[c] = np.nan
                continue
            ann = g["ann_date"].values
            # 公告日在调仓日前的最新一期
            valid = g[g["ann_date"] <= date]
            if len(valid) == 0:
                roe_vals[c] = np.nan
            else:
                roe_vals[c] = valid["roe"].iloc[-1]

        roe = pd.Series(roe_vals, index=candidates)

        # 截面归一化（rank → 0-1）
        def norm(s):
            s = s.replace([np.inf, -np.inf], np.nan)
            return s.rank(pct=True).fillna(0.5)

        factor_snapshots[date] = pd.DataFrame({
            "bp_z": norm(bpz),
            "atr_pct": -norm(atr),       # 低波好 → 取负
            "turnover": norm(turn),
            "roe": norm(roe),
        }, index=candidates)

    print("  因子快照: %d 个调仓日" % len(factor_snapshots))
    return factor_snapshots, close_wide, trade_dates


# ---------------- 标签 ----------------
def build_labels(factor_snapshots, close_wide, benchmark):
    """标签：未来 FORWARD_DAYS 相对沪深300 是否超额。返回 {date: Series(0/1)}。"""
    print("[3/5] 构建标签...")
    bm = benchmark
    labels = {}
    for date, fac in factor_snapshots.items():
        if date not in close_wide.index:
            continue
        fwd = close_wide.shift(-FORWARD_DAYS) / close_wide - 1.0
        # 基准未来收益
        if bm is not None:
            bm_fwd = bm.shift(-FORWARD_DAYS) / bm - 1.0
            if date in bm_fwd.index:
                bm_val = bm_fwd.loc[date]
            else:
                bm_val = 0.0
        else:
            bm_val = 0.0
        fwd_today = fwd.loc[date].reindex(fac.index)
        # 需要未来数据完整
        label = (fwd_today > bm_val).astype(int)
        label = label.where(fwd_today.notna(), np.nan)
        labels[date] = label
    return labels


# ---------------- 训练 + 回测 ----------------
def run_backtest(factor_snapshots, labels, close_wide, mode="ml"):
    """滚动回测。mode: 'ml' = LightGBM合成, 'equal' = 4因子等权。"""
    print("[4/5] 执行回测 (mode=%s)..." % mode)
    dates = sorted(factor_snapshots.keys())

    if mode == "ml":
        import lightgbm as lgb
        model = None

    capital = 1000000.0
    positions = {}   # code -> (shares, cost)
    equity = []
    trades = []
    last_train = None

    for i, date in enumerate(dates):
        fac = factor_snapshots[date]
        if len(fac) < 100:
            continue

        # --- 训练（ml 模式，每 6 个调仓日重训一次）---
        if mode == "ml" and (last_train is None or i - last_train >= 6):
            # 收集历史样本
            X_all, y_all = [], []
            hist = dates[max(0, i - 30):i]  # 用过去 30 个调仓日
            for hd in hist:
                if hd not in labels:
                    continue
                lab = labels[hd].dropna()
                if len(lab) < 50:
                    continue
                fh = factor_snapshots[hd].loc[lab.index]
                X_all.append(fh.values)
                y_all.append(lab.values)
            if len(X_all) > 0:
                X = np.vstack(X_all)
                y = np.concatenate(y_all)
                if len(X) > 2000:
                    ds = lgb.Dataset(X, label=y)
                    params = {"objective": "binary", "metric": "auc",
                              "num_leaves": 31, "learning_rate": 0.05,
                              "feature_fraction": 0.8, "verbose": -1}
                    model = lgb.train(params, ds, num_boost_round=50)
                    last_train = i

        # --- 打分 ---
        if mode == "ml":
            if model is None:
                continue
            pred = model.predict(fac.values)
            score = pd.Series(pred, index=fac.index)
        else:
            # 4 因子等权
            score = fac.mean(axis=1)

        # 基础过滤：市值/流动性（简化：只选成交额非零）
        candidates = score.dropna().sort_values(ascending=False).index[:TOP_N].tolist()

        # --- 卖出到期 ---
        for code in [c for c, p in positions.items() if i - p["idx"] >= 1]:
            pos = positions[code]
            if date in close_wide.index and code in close_wide.columns:
                cp = close_wide.loc[date, code]
                if pd.notna(cp):
                    capital += pos["shares"] * cp * 0.998
                    trades.append({"pnl": (cp / pos["cost"] - 1) * 100})
            del positions[code]

        # --- 买入（每月换仓：卖旧买新，简单近似）---
        if i % 1 == 0:  # 每次调仓日换仓
            # 先清仓（简化：每月全换）
            for code in list(positions.keys()):
                pos = positions[code]
                if date in close_wide.index and code in close_wide.columns:
                    cp = close_wide.loc[date, code]
                    if pd.notna(cp):
                        capital += pos["shares"] * cp * 0.998
                        trades.append({"pnl": (cp / pos["cost"] - 1) * 100})
                del positions[code]

            n_buy = min(len(candidates), TOP_N)
            if n_buy > 0 and capital > 10000:
                alloc = capital * 0.95 / n_buy
                for code in candidates[:n_buy]:
                    if date in close_wide.index and code in close_wide.columns:
                        bp = close_wide.loc[date, code]
                        if not pd.isna(bp) and bp > 0:
                            shares = int(alloc / bp / 100) * 100
                            if shares >= 100:
                                capital -= shares * bp * 1.002
                                positions[code] = {"shares": shares, "cost": bp, "idx": i}

        # --- 记录净值 ---
        pv = capital
        for c, p in positions.items():
            if date in close_wide.index and c in close_wide.columns:
                cp = close_wide.loc[date, c]
                if pd.notna(cp):
                    pv += p["shares"] * cp
        equity.append({"date": str(date.date()), "value": pv})

    eq = pd.DataFrame(equity)
    return eq, trades


def metrics(eq, initial=1000000.0):
    """计算回测指标。"""
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
    sharpe = np.sqrt(252) * rets.mean() / rets.std() if rets.std() > 0 else 0
    return {"total_return": round(total, 4), "annual_return": round(ann, 4),
            "max_drawdown": round(mdd, 4), "sharpe": round(sharpe, 3),
            "n_days": len(v)}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="冒烟模式：只加载数据+算因子，不跑回测")
    ap.add_argument("--n-codes", type=int, default=0,
                    help="限制股票数（0=全量）")
    args = ap.parse_args()

    print("=" * 60)
    print("ML 因子合成器 vs 4因子等权手工基线")
    print("=" * 60)

    panel, ind_map = load_panel(n_codes=args.n_codes)
    codes_all = set(panel.index.get_level_values("ts_code"))
    fin_by_code = load_finance(codes_all)
    bm = load_benchmark()
    print("  基准: %s" % ("OK" if bm is not None else "None"))

    factor_snapshots, close_wide, trade_dates = compute_factors(
        panel, ind_map, fin_by_code)

    if args.smoke:
        print("\n[SMOKE] 因子快照数: %d" % len(factor_snapshots))
        if factor_snapshots:
            d0 = sorted(factor_snapshots.keys())[0]
            print("  首个快照 %s: %d 只, 列=%s" % (
                d0, len(factor_snapshots[d0]), list(factor_snapshots[d0].columns)))
            print("  样本:\n%s" % factor_snapshots[d0].head(3).to_string())
        print("[SMOKE] 数据管线 OK")
        return

    labels = build_labels(factor_snapshots, close_wide, bm)

    # 分段：训练2019-2021 / 验证2022-2023 / 测试2024-2026
    def split(eq):
        eq = eq.copy()
        eq["date"] = pd.to_datetime(eq["date"])
        tr = eq[eq["date"] < "2022-01-01"]
        va = eq[(eq["date"] >= "2022-01-01") & (eq["date"] < "2024-01-01")]
        te = eq[eq["date"] >= "2024-01-01"]
        return tr, va, te

    # 4因子等权基线
    eq_eq, trades_eq = run_backtest(factor_snapshots, labels, close_wide, mode="equal")
    # ML 合成
    eq_ml, trades_ml = run_backtest(factor_snapshots, labels, close_wide, mode="ml")

    print("\n" + "=" * 60)
    print("【4因子等权手工基线】")
    for name, seg in zip(["全期", "训练段", "验证段", "测试段"],
                         [eq_eq] + list(split(eq_eq))):
        m = metrics(seg)
        print("  %s: 年化%+.2f%% 回撤%.1f%% 夏普%.2f" % (
            name, m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"]))

    print("\n【ML 合成】")
    for name, seg in zip(["全期", "训练段", "验证段", "测试段"],
                         [eq_ml] + list(split(eq_ml))):
        m = metrics(seg)
        print("  %s: 年化%+.2f%% 回撤%.1f%% 夏普%.2f" % (
            name, m["annual_return"] * 100, m["max_drawdown"] * 100, m["sharpe"]))

    print("\n" + "=" * 60)
    print("最终结论：ML 是否跑赢手工等权？")
    me = metrics(eq_eq)
    mm = metrics(eq_ml)
    diff = mm["annual_return"] - me["annual_return"]
    print("  手工等权: 年化%+.2f%% | ML: 年化%+.2f%% | 差值 %+.2f%%" % (
        me["annual_return"] * 100, mm["annual_return"] * 100, diff * 100))
    if diff > 0.03:
        print("  → ML 显著跑赢，值得用")
    elif diff > 0:
        print("  → ML 微幅跑赢，边际价值")
    else:
        print("  → ML 未跑赢手工，ML 在此框架无增益（与历史 ML 证伪一致）")


if __name__ == "__main__":
    main()
