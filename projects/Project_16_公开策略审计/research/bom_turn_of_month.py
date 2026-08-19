# coding: utf-8
"""BOM 月末效应复现（T-20260819-001 任务A）。

原文（知乎第1篇「向南」）：上证指数 2000-01-01~2024-01-01
  V1 书版：收盘>40日均线 且 当日=自然月最后交易日 → 尾盘(收盘)买入；
           持有期间某日收盘<40日均线 → 该日尾盘提前卖出；
           否则持有第4个交易日尾盘卖出（买入日记为第1个持有交易日，总敞口4天）。
  V2 实盘版：去掉 40MA 条件，其余同。
 敏感性变体：持有第5个交易日卖出。

口径：
  * 主口径=收盘撮合；敏感性=次日开盘撮合（买入价=次日开盘，卖出价=卖出日开盘）。
  * 成本：ETF 佣金单边万1（round trip 0.0002）+ 零成本对照；指数口径无成本概念。
  * 效应存在性：月末窗口=T-1..T+3（5日）日均收益 vs 窗口外；全样本+分半样本 + t 值。
  * 全部只用已实现价格，无未来信息（月末判定用交易日历，盘中可知）。
"""
import datetime as dt
import os

import duckdb
import numpy as np
import pandas as pd

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(PROJ, "results")
os.makedirs(RESULTS, exist_ok=True)

ETF_CSV = r"D:/QuantLab/data/etf_daily/510300_SH.csv"
DB_PATH = r"F:/backtest_workspace/data/duckdb/benchmark_index.duckdb"
ETF_COST = 0.0001 * 2  # 佣金单边万1，ETF 免印花税，round trip
INDEX_START = "2009-01-05"
INDEX_END = "2026-06-12"


# ---------------------------------------------------------------- data
def load_index_ohlc(code):
    con = duckdb.connect(DB_PATH, read_only=True)
    df = con.execute(
        "SELECT trade_date AS date, open, high, low, close FROM index_daily "
        "WHERE code = ? AND source = 'xtquant' AND trade_date BETWEEN ? AND ? "
        "ORDER BY trade_date",
        [code, INDEX_START, INDEX_END]).fetchdf()
    con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df.reset_index(drop=True)


def load_etf_ohlc():
    df = pd.read_csv(ETF_CSV, parse_dates=["date"])
    df = df[["date", "open", "high", "low", "close"]].sort_values("date").reset_index(drop=True)
    return df


# ---------------------------------------------------------------- engine
def bom_engine(df, mode="v2", fill="close", hold_days=4, ma_window=40, cost=0.0):
    """返回 trade 记录 DataFrame。

    规则（把原文规则落到逐日循环）：
      * month_end：每个自然月最后一个交易日 = 买入信号日 T。
      * V1：仅当 close(T) > MA40(T) 时触发；V2 恒触发。
      * 收盘撮合：buy_price=close(T)，buy_day=T；
                  持有期 day T+k (k=1..hold_days-1) 若 close < MA40 → sell close(T+k) 提前；
                  否则 sell close(T+hold_days-1)。
      * 开盘撮合：buy_price=open(T+1)，buy_day=T+1；
                  持有期 day T+k (k=1..hold_days-1) 若 close(T+k) < MA40(T+k) → sell open(T+k+1) 提前；
                  否则 sell open(T+1+hold_days-1)=open(T+hold_days)。
      * 收益 gross= sell/buy-1；net = gross - cost(round trip)。
    """
    dates = pd.to_datetime(df["date"]).dt.date.to_numpy()
    px = df["close"].to_numpy(dtype=float)
    op = df["open"].to_numpy(dtype=float)
    n = len(df)
    ma = pd.Series(px).rolling(ma_window, min_periods=ma_window).mean().to_numpy()

    period = pd.to_datetime(df["date"]).dt.to_period("M")
    g = pd.DataFrame({"p": period}).groupby("p")
    month_end_idx = [grp.index[-1] for _, grp in g]

    trades = []
    for i in month_end_idx:
        if mode == "v1" and not (np.isfinite(ma[i]) and px[i] > ma[i]):
            continue
        if fill == "close":
            buy_price = px[i]
            buy_day = i
            last_day = i + hold_days - 1
            if last_day >= n:
                continue
            sell_day = last_day
            # 提前卖出检查：持有期 T+1..T+hold_days-1 收盘<MA
            early = None
            if mode == "v1":  # V2 无 40MA 条件，固定持有，不提前卖
                for k in range(1, hold_days):
                    j = i + k
                    if j >= n:
                        break
                    if np.isfinite(ma[j]) and px[j] < ma[j]:
                        early = j
                        break
            if early is not None:
                sell_day = early
            sell_price = px[sell_day]
        else:  # open
            buy_day = i + 1
            if buy_day >= n:
                continue
            buy_price = op[buy_day]
            last_day = buy_day + hold_days - 1
            if last_day >= n:
                continue
            sell_day = last_day
            early = None
            if mode == "v1":
                for k in range(1, hold_days):
                    j = i + k
                    if j >= n:
                        break
                    if np.isfinite(ma[j]) and px[j] < ma[j]:
                        early = j
                        break
            if early is not None:
                sell_day = early + 1
                if sell_day >= n:
                    continue
            sell_price = op[sell_day]
        gross = sell_price / buy_price - 1.0
        net = gross - cost
        trades.append({
            "month": str(period.iloc[i]),
            "T_date": dates[i],
            "buy_date": dates[buy_day],
            "sell_date": dates[sell_day],
            "buy_price": round(float(buy_price), 6),
            "sell_price": round(float(sell_price), 6),
            "gross": round(float(gross), 6),
            "net": round(float(net), 6),
        })
    return pd.DataFrame(trades)


def kpis(trades_df):
    if trades_df is None or len(trades_df) == 0:
        return {}
    rets = trades_df["net"].to_numpy()
    ntr = len(rets)
    win = float((rets > 0).mean())
    avg_win = float(rets[rets > 0].mean()) if (rets > 0).any() else 0.0
    avg_loss = float(rets[rets < 0].mean()) if (rets < 0).any() else 0.0
    # 组合净值 = 逐笔复利（持仓期外现金）
    equity = np.cumprod(1 + rets)
    total = equity[-1] - 1
    days_total = int(trades_df["sell_date"].max().toordinal() - trades_df["buy_date"].min().toordinal())
    years = max(days_total / 365.25, 0.5)
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    peak = np.maximum.accumulate(equity)
    mdd = float(((equity - peak) / peak).min())
    # 年化波动/夏普（用持仓期日收益，粗略；注明方法局限）
    ann_trades = ntr / years
    std = float(rets.std(ddof=1)) if ntr > 1 else 0.0
    sharpe = (float(rets.mean()) / std * np.sqrt(ann_trades)) if std > 0 else 0.0
    return {
        "n_trades": ntr, "win_rate": win, "avg_win": avg_win, "avg_loss": avg_loss,
        "total": total, "cagr": cagr, "max_dd": mdd, "sharpe": sharpe, "years": years,
    }


# ---------------------------------------------------------------- effect test
def effect_existence(df, split1_end):
    """月末窗口 T-1..T+3 日均收益 vs 窗口外；全样本+分半。返回 dict of DataFrame。"""
    dates = pd.to_datetime(df["date"])
    px = df["close"].to_numpy(dtype=float)
    r = pd.Series(np.full(len(df), np.nan))
    r.iloc[1:] = px[1:] / px[:-1] - 1.0
    # 月末 T
    period = dates.dt.to_period("M")
    g = pd.DataFrame({"p": period}).groupby("p")
    month_end_idx = set(grp.index[-1] for _, grp in g)
    window = set()
    for i in month_end_idx:
        for j in range(i - 1, i + 4):
            if 0 <= j < len(df):
                window.add(j)
    w = pd.Series([1 if i in window else 0 for i in range(len(df))], index=df.index)
    dfx = pd.DataFrame({"ret": r, "win": w, "date": dates})
    out = {}
    mask = dfx["win"] == 1
    for label, sub in [("full", dfx), ("half1", dfx[dfx["date"] <= split1_end]),
                       ("half2", dfx[dfx["date"] > split1_end])]:
        if len(sub) < 5:
            continue
        inw = sub.loc[sub["win"] == 1, "ret"].dropna()
        outw = sub.loc[sub["win"] == 0, "ret"].dropna()
        m_in, m_out = float(inw.mean()), float(outw.mean())
        n1, n2 = len(inw), len(outw)
        s1, s2 = float(inw.std(ddof=1)), float(outw.std(ddof=1))
        denom = np.sqrt(s1 ** 2 / n1 + s2 ** 2 / n2) if n1 > 1 and n2 > 1 else 0.0
        t = (m_in - m_out) / denom if denom > 0 else np.nan
        out[label] = {
            "n_in": n1, "n_out": n2, "mean_in": m_in, "mean_out": m_out,
            "diff": m_in - m_out, "t": float(t),
        }
    return out


def monthly_window_detail(df, label):
    """逐月窗口收益明细 CSV：T 日 + 窗口内每日收益。"""
    dates = pd.to_datetime(df["date"])
    px = df["close"].to_numpy(dtype=float)
    r = pd.Series(np.full(len(df), np.nan))
    r.iloc[1:] = px[1:] / px[:-1] - 1.0
    period = dates.dt.to_period("M")
    g = pd.DataFrame({"p": period}).groupby("p")
    rows = []
    for p, grp in g:
        i = grp.index[-1]
        t = dates.iloc[i].strftime("%Y-%m-%d")
        wins = {}
        for j, off in zip(range(i - 1, i + 4), ["T-1", "T", "T+1", "T+2", "T+3"]):
            if 0 <= j < len(df):
                wins[off] = r.iloc[j]
            else:
                wins[off] = np.nan
        rows.append({"label": label, "month": str(p), "T_date": t, **wins})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- main
def main():
    out = []
    instruments = {
        "HS300": load_index_ohlc("000300.SH"),
        "SSE": load_index_ohlc("000001.SH"),
        "ETF510300": load_etf_ohlc(),
    }

    print("=" * 80)
    print("任务A：BOM 月末效应复现（T-20260819-001）")
    print("=" * 80)

    # ---- 效应存在性检验 ----
    print("\n## 一、效应存在性（月末窗口 T-1..T+3 日均收益 vs 窗口外）")
    effect = {}
    for label, dfx in instruments.items():
        split = "2017-12-31" if "ETF" not in label else "2020-12-31"
        effect[label] = effect_existence(dfx, split)
        e = effect[label]
        print(f"\n[{label}] 区间 {dfx['date'].min().date()} ~ {dfx['date'].max().date()}  {len(dfx)} 行")
        for k in ["full", "half1", "half2"]:
            if k in e:
                v = e[k]
                print(f"  {k:6s} n_in={v['n_in']:4d} n_out={v['n_out']:5d} "
                      f"in={v['mean_in']*100:+.3f}% out={v['mean_out']*100:+.3f}% "
                      f"diff={v['diff']*100:+.3f}% t={v['t']:+.2f}")

    # ---- 逐月窗口明细落盘 ----
    frames = []
    for label, dfx in instruments.items():
        frames.append(monthly_window_detail(dfx, label))
    mw = pd.concat(frames, ignore_index=True)
    mw.to_csv(os.path.join(RESULTS, "bom_monthly_window_returns.csv"), index=False, encoding="utf-8-sig")
    print(f"\n[落盘] bom_monthly_window_returns.csv  {len(mw)} 行")

    # ---- 锚1：ETF 与 HS300 窗口日均收益相关性 ----
    etf_d = mw[mw["label"] == "ETF510300"][["month", "T-1", "T", "T+1", "T+2", "T+3"]].rename(
        columns={c: "e_" + c for c in ["T-1", "T", "T+1", "T+2", "T+3"]})
    idx_d = mw[mw["label"] == "HS300"][["month", "T-1", "T", "T+1", "T+2", "T+3"]].rename(
        columns={c: "i_" + c for c in ["T-1", "T", "T+1", "T+2", "T+3"]})
    mrg = etf_d.merge(idx_d, on="month", how="inner")
    corr_cols = []
    for c in ["T-1", "T", "T+1", "T+2", "T+3"]:
        cc = float(mrg["e_" + c].corr(mrg["i_" + c]))
        corr_cols.append(cc)
    corr_avg = float(np.mean(corr_cols))
    print(f"\n[锚1] ETF vs HS300 月末窗口日均收益逐日相关: {[round(c,3) for c in corr_cols]} 均值={corr_avg:.3f} (要求>0.8) -> {'PASS' if corr_avg>0.8 else 'FAIL'}")
    # 全窗口日收益合并相关（并排所有窗口日）
    all_win = pd.DataFrame({"etf": pd.concat([mrg["e_"+c] for c in ["T-1","T","T+1","T+2","T+3"]]).dropna().reset_index(drop=True)})
    # 简化：逐窗口相关均值即锚

    # ---- 策略回测矩阵 ----
    print("\n## 二、策略净值矩阵（V1/V2 × 收盘/开盘 × 有/无成本 × 指数/ETF）")
    matrix = []
    for label, dfx in instruments.items():
        is_etf = label == "ETF510300"
        costs = [0.0, ETF_COST] if is_etf else [0.0]
        for mode in ["v1", "v2"]:
            for fill in ["close", "open"]:
                for cost in costs:
                    for hold_days, hname in [(4, "4d"), (5, "5d")]:
                        tr = bom_engine(dfx, mode=mode, fill=fill, hold_days=hold_days, cost=cost)
                        k = kpis(tr)
                        if not k:
                            continue
                        matrix.append({
                            "instrument": label, "mode": mode.upper(), "fill": fill,
                            "cost": cost, "hold": hname, **k,
                        })
                        print(f"  {label:9s} {mode.upper()} {fill:5s} hold={hname} cost={cost:.4f} "
                              f"n={k['n_trades']:3d} CAGR={k['cagr']*100:+.2f}% "
                              f"MDD={k['max_dd']*100:+.1f}% Sharpe={k['sharpe']:.2f} "
                              f"win={k['win_rate']*100:.0f}% avgWin={k['avg_win']*100:+.2f}% avgLoss={k['avg_loss']*100:+.2f}%")

    mat = pd.DataFrame(matrix)
    mat.to_csv(os.path.join(RESULTS, "bom_strategy_matrix.csv"), index=False, encoding="utf-8-sig")

    # ---- 锚2：V2 年均交易次数 ≈ 12 ----
    for label in ["HS300", "SSE", "ETF510300"]:
        tr = bom_engine(instruments[label], mode="v2", fill="close")
        y = tr["buy_date"].apply(lambda d: d.year)
        per_year = y.value_counts().sort_index()
        avg = float(per_year.mean())
        yrs = tr["buy_date"].max().year - tr["buy_date"].min().year + 1
        print(f"\n[锚2] {label} V2 交易次数={len(tr)} 年均={avg:.2f} (要求≈12) -> {'PASS' if 6<avg<18 else 'FAIL'}")

    # ---- 锚3：随机抽3个月人工核对（落盘供核） ----
    rng = np.random.RandomState(42)
    for label in ["ETF510300", "HS300"]:
        tr = bom_engine(instruments[label], mode="v2", fill="close")
        pick = tr["month"].sample(3, random_state=7).tolist()
        sub = tr[tr["month"].isin(pick)]
        print(f"\n[锚3] {label} 抽样月核对：")
        print(sub[["month", "T_date", "buy_date", "sell_date", "buy_price", "sell_price", "gross"]].to_string(index=False))

    # ---- 锚4：B&H 对照 ----
    print("\n## 三、B&H 对照（同区间）")
    for label, dfx in instruments.items():
        px0 = dfx["close"].iloc[0]
        pxn = dfx["close"].iloc[-1]
        days = (dfx["date"].iloc[-1] - dfx["date"].iloc[0]).days / 365.25
        bh = pxn / px0 - 1
        bh_cagr = (pxn / px0) ** (1 / days) - 1
        print(f"  {label:9s} {dfx['date'].iloc[0].date()}~{dfx['date'].iloc[-1].date()} "
              f"B&H总={bh*100:+.1f}% CAGR={bh_cagr*100:+.2f}%")

    print("\n完成。产物：bom_monthly_window_returns.csv / bom_strategy_matrix.csv")


if __name__ == "__main__":
    main()