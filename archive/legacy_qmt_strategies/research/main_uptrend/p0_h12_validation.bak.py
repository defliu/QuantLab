# -*- coding: utf-8 -*-
"""P0 H12 validation script - main uptrend conditional probability.

Data access: positional alias to avoid GBK byte encoding conflicts.
Indicators: pure numpy/pandas vectorized (no pandas-ta dependency).
"""
import datetime as dt
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# ============================================================
# 1. Data access layer
# ============================================================
DB_PATH = "E:/huicexitong/runtime/sj/gpsj.duckdb"
REPORT_PATH = Path("D:/QMT_STRATEGIES/research/main_uptrend/reports/p0_h12_report.md")


def open_db():
    return duckdb.connect(DB_PATH, read_only=True)


def find_daily_table(con):
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='daily_data'"
    ).fetchall()
    for (name,) in rows:
        n_cols = con.execute(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema='daily_data' AND table_name=?",
            [name],
        ).fetchone()[0]
        if n_cols == 117:
            return name
    raise RuntimeError("daily_data table not found (expected 117 columns)")


ALIASES = ["code", "date", "open", "close", "high", "low",
           "prev_close", "chg_amount", "chg_pct", "volume_share",
           "amount_kyuan", "factor"]


def load_daily(con, table_raw, start_date, end_date):
    cols = con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='daily_data' AND table_name=? ORDER BY ordinal_position",
        [table_raw],
    ).fetchall()
    select_parts = []
    for i in range(12):
        raw = cols[i][0]
        select_parts.append(f'"{raw}" AS {ALIASES[i]}')
    select_sql = ", ".join(select_parts)
    q = (
        f"SELECT {select_sql} FROM daily_data.\"{table_raw}\" "
        f"WHERE date BETWEEN ? AND ? "
        f"ORDER BY code, date"
    )
    return con.execute(q, [start_date, end_date]).fetchdf()


# ============================================================
# 2. Adjusted prices
# ============================================================

def to_adjusted(df):
    df = df.copy()
    df["factor_last"] = df.groupby("code")["factor"].transform("last")
    ratio = df["factor"] / df["factor_last"]
    for col in ["open", "close", "high", "low"]:
        df[f"adj_{col}"] = df[col] * ratio
    return df


# ============================================================
# 3. Vectorized indicators
# ============================================================

def _wilder_smooth(arr, period):
    out = np.empty_like(arr, dtype=float)
    out[:] = np.nan
    start = 0
    while start < len(arr) and np.isnan(arr[start]):
        start += 1
    end = start + period
    if end > len(arr):
        return out
    out[end - 1] = np.mean(arr[start:end])
    for i in range(end, len(arr)):
        out[i] = (out[i - 1] * (period - 1) + arr[i]) / period
    return out


def _rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = _wilder_smooth(gain, period)
    avg_loss = _wilder_smooth(loss, period)
    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(high, low, close, period=14):
    prev_c = np.roll(close, 1)
    prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    return _wilder_smooth(tr, period)


def _adx(high, low, close, period=14):
    up_move = np.diff(high, prepend=high[0])
    down_move = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_c = np.roll(close, 1)
    prev_c[0] = close[0]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    tr[0] = high[0] - low[0]
    atr = _wilder_smooth(tr, period)
    plus_di = 100.0 * _wilder_smooth(plus_dm, period) / np.where(atr == 0, 1e-10, atr)
    minus_di = 100.0 * _wilder_smooth(minus_dm, period) / np.where(atr == 0, 1e-10, atr)
    dx = 100.0 * np.abs(plus_di - minus_di) / np.where((plus_di + minus_di) == 0, 1e-10, plus_di + minus_di)
    return _wilder_smooth(dx, period)


def _rolling_percentile(arr, window=252):
    """Vectorized rolling percentile rank using pandas."""
    s = pd.Series(arr)
    def pct_rank(x):
        valid = x.dropna()
        if len(valid) == 0:
            return np.nan
        return (valid < x.iloc[-1]).sum() / len(valid) * 100.0
    return s.rolling(window, min_periods=window).apply(pct_rank, raw=False).values


def _linear_slope_rolling(arr, window=5):
    """Rolling linear regression slope."""
    n = len(arr)
    out = np.full(n, np.nan)
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    for i in range(window - 1, n):
        y = arr[i - window + 1:i + 1]
        if np.any(np.isnan(y)):
            continue
        out[i] = ((x - x_mean) * (y - y.mean())).sum() / x_var
    return out


def compute_trend_score(df):
    """Per-stock trend strength score (0-100)."""
    n = len(df)
    if n < 60:
        return pd.Series(np.full(n, np.nan), index=df.index)

    close = df["adj_close"].values.astype(float)
    high = df["adj_high"].values.astype(float)
    low = df["adj_low"].values.astype(float)
    vol = df["volume_share"].values.astype(float)

    # F1: ADX(14)
    adx = _adx(high, low, close, 14)
    f1 = np.clip((adx - 20.0) / 20.0, 0.0, 1.0) * 100.0

    # F2: MA5 slope percentile
    ma5 = pd.Series(close).rolling(5).mean().values
    slope5 = _linear_slope_rolling(ma5, 5)
    f2 = _rolling_percentile(slope5, 252)

    # F3: MA divergence percentile
    ma20 = pd.Series(close).rolling(20).mean().values
    divergence = (ma5 - ma20) / np.where(ma20 == 0, 1e-10, ma20)
    f3 = _rolling_percentile(divergence, 252)

    # F4: RSI(14) duration
    rsi = _rsi(close, 14)
    rsi_above50 = (rsi > 50.0).astype(float)
    f4 = pd.Series(rsi_above50).rolling(20, min_periods=20).mean().values * 100.0

    # F5: Price position (52W)
    hi52 = pd.Series(high).rolling(252, min_periods=252).max().values
    lo52 = pd.Series(low).rolling(252, min_periods=252).min().values
    rng52 = hi52 - lo52
    f5 = np.where(rng52 > 0, (close - lo52) / rng52 * 100.0, 50.0)
    f5[:251] = np.nan

    # F6: ATR compression + breakout
    atr14 = _atr(high, low, close, 14)
    atr60_mean = pd.Series(atr14).rolling(60, min_periods=60).mean().values
    vol20_mean = pd.Series(vol).rolling(20, min_periods=20).mean().values
    ratio_atr = np.where(atr60_mean > 0, atr14 / atr60_mean, 1.0)
    ratio_vol = np.where(vol20_mean > 0, vol / vol20_mean, 1.0)
    f6 = np.where(
        (ratio_atr <= 0.8) & (ratio_vol > 1.3), 100.0,
        np.where(ratio_atr > 0, np.clip(100.0 * (1.0 - ratio_atr), 0.0, 100.0), 0.0)
    )
    f6[:59] = np.nan

    # F7: OBV divergence
    sign = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(sign * vol)
    price_20high = pd.Series(close).rolling(20, min_periods=20).max().values
    obv_20high = pd.Series(obv).rolling(20, min_periods=20).max().values
    f7 = np.where(
        (close >= price_20high) & (obv < obv_20high), 85.0, 100.0
    )
    f7[:19] = np.nan

    # Composite score
    weights = np.array([0.20, 0.15, 0.15, 0.10, 0.10, 0.15, 0.15])
    raw = np.column_stack([f1, f2, f3, f4, f5, f6, f7])
    clamped = np.clip(raw, 0.0, 100.0)
    total = np.nansum(clamped * weights, axis=1)
    # NaN out rows where any factor is NaN
    any_nan = np.any(np.isnan(raw), axis=1)
    total[any_nan] = np.nan

    # F5 price position rules
    f5_pct = f5 / 100.0
    total[f5_pct > 0.9] = np.nan
    mask_80 = (f5_pct > 0.8) & (f5_pct <= 0.9)
    total[mask_80] -= 20.0

    return pd.Series(total, index=df.index)


# ============================================================
# 4. Main uptrend label
# ============================================================

RISE_THRESHOLDS = [0.25, 0.30, 0.35]
DRAWDOWN_THRESHOLDS = [0.10, 0.12, 0.15]
WINDOW_DAYS = 60


def label_main_uptrend_fast(close_arr, rise_th, dd_th):
    """Vectorized: for each T0, check if max gain in T0+1..T0+60 >= rise_th and max dd <= dd_th."""
    n = len(close_arr)
    label = np.zeros(n, dtype=int)
    for i in range(n - 1):
        end = min(i + WINDOW_DAYS + 1, n)
        future = close_arr[i + 1:end]
        if len(future) < 10:
            continue
        max_gain = np.max(future) / close_arr[i] - 1.0
        if max_gain < rise_th:
            continue
        peak = close_arr[i]
        max_dd = 0.0
        for j in range(len(future)):
            peak = max(peak, future[j])
            dd = 1.0 - future[j] / peak
            if dd > max_dd:
                max_dd = dd
        if max_dd <= dd_th:
            label[i] = 1
    return label


# ============================================================
# 5. Main
# ============================================================

def main():
    print("=" * 60)
    print("P0 H12 main uptrend conditional probability validation")
    print("=" * 60)

    con = open_db()
    table_raw = find_daily_table(con)
    print(f"Table: {repr(table_raw)}")

    start_date = dt.date(2019, 1, 1)
    end_date = dt.date(2023, 12, 31)
    print(f"Loading {start_date} ~ {end_date} ...")
    t0 = time.time()
    df = load_daily(con, table_raw, start_date, end_date)
    print(f"Loaded {len(df):,} rows in {time.time() - t0:.1f}s")
    con.close()

    print("Adjusting prices ...")
    df = to_adjusted(df)

    print("Computing trend scores ...")
    t0 = time.time()
    codes = df["code"].unique()
    n_codes = len(codes)
    print(f"  {n_codes} stocks")
    score_chunks = []
    for idx, code in enumerate(codes):
        mask = df["code"] == code
        scores = compute_trend_score(df.loc[mask])
        score_chunks.append(scores)
        if (idx + 1) % 500 == 0:
            print(f"  {idx + 1}/{n_codes}")
    df["trend_score"] = pd.concat(score_chunks).values
    print(f"  Done in {time.time() - t0:.1f}s")

    # Candidates: trend_score >= 60
    candidates = df[df["trend_score"] >= 60.0].copy()
    print(f"\nCandidates (score>=60): {len(candidates):,}")

    # Precompute adj_close array for label function
    adj_close_all = df["adj_close"].values.astype(float)
    code_arr = df["code"].values
    idx_arr = df.index.values

    # 9-group sensitivity
    print("\n--- 9-group sensitivity ---")
    results = []
    default_row = None

    for rise_th in RISE_THRESHOLDS:
        for dd_th in DRAWDOWN_THRESHOLDS:
            print(f"  rise={rise_th}, dd={dd_th} ...", end=" ", flush=True)
            t1 = time.time()

            cand_indices = candidates.index.values
            labels = np.zeros(len(cand_indices), dtype=int)

            # Group candidates by code for labeling
            cand_codes = candidates["code"].values
            unique_cand_codes = np.unique(cand_codes)

            gains_list = []
            losses_list = []

            for ccode in unique_cand_codes:
                c_mask = cand_codes == ccode
                c_indices = cand_indices[c_mask]
                # Get full stock data indices in df
                stock_mask = code_arr == ccode
                stock_indices = idx_arr[stock_mask]
                stock_close = adj_close_all[stock_mask]

                # Map cand_indices to local positions in stock_close
                idx_to_pos = {v: k for k, v in enumerate(stock_indices)}

                for ci in c_indices:
                    if ci not in idx_to_pos:
                        continue
                    local_i = idx_to_pos[ci]
                    end = min(local_i + WINDOW_DAYS + 1, len(stock_close))
                    future = stock_close[local_i + 1:end]
                    if len(future) < 10:
                        continue
                    max_gain = np.max(future) / stock_close[local_i] - 1.0
                    if max_gain < rise_th:
                        continue
                    peak = stock_close[local_i]
                    max_dd = 0.0
                    for fj in range(len(future)):
                        peak = max(peak, future[fj])
                        dd = 1.0 - future[fj] / peak
                        if dd > max_dd:
                            max_dd = dd
                    if max_dd <= dd_th:
                        gains_list.append(max_gain)
                    else:
                        losses_list.append(-max_dd)

            n_cand = len(cand_indices)
            n_pos = len(gains_list)
            p_val = n_pos / n_cand if n_cand > 0 else 0.0
            avg_gain = np.mean(gains_list) if gains_list else 0.0
            avg_loss = np.mean(losses_list) if losses_list else 0.0
            wl = abs(avg_gain / avg_loss) if avg_loss != 0 else float("inf")

            row = {
                "rise_th": rise_th,
                "dd_th": dd_th,
                "N_candidate": n_cand,
                "P_main_uptrend": round(p_val, 4),
                "fake_breakout": round(1 - p_val, 4),
                "avg_gain_true": round(avg_gain, 4),
                "avg_loss_false": round(avg_loss, 4),
                "win_loss_ratio": round(wl, 2),
            }
            results.append(row)
            if rise_th == 0.30 and dd_th == 0.12:
                default_row = row

            print(f"N={n_cand:,}, P={p_val:.2%}, W/L={wl:.2f} ({time.time()-t1:.1f}s)")

    # Default group yearly breakdown
    print("\nDefault group (30%/12%) yearly ...")
    cand_indices = candidates.index.values
    cand_codes = candidates["code"].values
    unique_cand_codes = np.unique(cand_codes)
    default_labels = np.zeros(len(cand_indices), dtype=int)

    for ccode in unique_cand_codes:
        c_mask = cand_codes == ccode
        c_indices = cand_indices[c_mask]
        stock_mask = code_arr == ccode
        stock_indices = idx_arr[stock_mask]
        stock_close = adj_close_all[stock_mask]
        idx_to_pos = {v: k for k, v in enumerate(stock_indices)}

        for ci in c_indices:
            if ci not in idx_to_pos:
                continue
            local_i = idx_to_pos[ci]
            end = min(local_i + WINDOW_DAYS + 1, len(stock_close))
            future = stock_close[local_i + 1:end]
            if len(future) < 10:
                continue
            max_gain = np.max(future) / stock_close[local_i] - 1.0
            if max_gain < 0.30:
                continue
            peak = stock_close[local_i]
            max_dd = 0.0
            for fj in range(len(future)):
                peak = max(peak, future[fj])
                dd = 1.0 - future[fj] / peak
                if dd > max_dd:
                    max_dd = dd
            if max_dd <= 0.12:
                default_labels[np.where(c_mask)[0][np.where(c_indices == ci)[0][0]]] = 1

    candidates_copy = candidates.copy()
    candidates_copy["label"] = default_labels
    candidates_copy["year"] = pd.to_datetime(candidates_copy["date"]).dt.year
    yearly = candidates_copy.groupby("year").agg(
        N_candidate=("label", "count"),
        N_positive=("label", "sum"),
    ).reset_index()
    yearly["P"] = (yearly["N_positive"] / yearly["N_candidate"]).round(4)

    # ============================================================
    # 6. Report
    # ============================================================
    print("\nGenerating report ...")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# P0 H12 main uptrend conditional probability report")
    lines.append("")
    lines.append(f"**Generated**: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    lines.append("## 1. Data")
    lines.append("")
    lines.append(f"- DB: {DB_PATH}")
    lines.append(f"- Table: {repr(table_raw)} (117 cols)")
    lines.append(f"- Period: {start_date} ~ {end_date}")
    lines.append(f"- Stocks: {n_codes}")
    lines.append(f"- Rows: {len(df):,}")
    lines.append(f"- Candidates (score>=60): {len(candidates):,}")
    lines.append("")

    lines.append("## 2. Indicators")
    lines.append("")
    lines.append("7-factor trend strength score:")
    lines.append("- F1: ADX(14) clamp[20,40] linear -> x0.20")
    lines.append("- F2: MA5 slope 252d rolling percentile -> x0.15")
    lines.append("- F3: (MA5-MA20)/MA20 252d rolling percentile -> x0.15")
    lines.append("- F4: RSI(14) >50 duration (20d) -> x0.10")
    lines.append("- F5: Price position 52W; >0.8 -20pt; >0.9 invalid -> x0.10")
    lines.append("- F6: ATR compression+vol breakout -> x0.15")
    lines.append("- F7: OBV divergence -> x0.15")
    lines.append("")
    lines.append("Label: T0+60d max gain >= rise_th AND max drawdown <= dd_th")
    lines.append("")

    lines.append("## 3. 9-group sensitivity")
    lines.append("")
    lines.append("| rise_th | dd_th | N_candidate | P(main) | fake_breakout | avg_gain | avg_loss | W/L ratio |")
    lines.append("|---------|-------|-------------|---------|---------------|----------|----------|-----------|")
    for r in results:
        lines.append(
            f"| {r['rise_th']:.2f} | {r['dd_th']:.2f} | {r['N_candidate']:,} | "
            f"{r['P_main_uptrend']:.2%} | {r['fake_breakout']:.2%} | "
            f"{r['avg_gain_true']:.2%} | {r['avg_loss_false']:.2%} | {r['win_loss_ratio']:.2f} |"
        )
    lines.append("")

    lines.append("## 4. Default group (30%/12%) by year")
    lines.append("")
    lines.append("| year | N_candidate | N_positive | P(main) |")
    lines.append("|------|-------------|------------|---------|")
    for _, row in yearly.iterrows():
        lines.append(f"| {int(row['year'])} | {int(row['N_candidate']):,} | {int(row['N_positive']):,} | {row['P']:.2%} |")
    lines.append("")

    lines.append("## 5. Conclusion")
    lines.append("")
    if default_row:
        wl = default_row["win_loss_ratio"]
        verdict = "PASS" if wl > 2.5 else "FAIL"
        lines.append(
            f"Default (rise=30%, dd=12%): P={default_row['P_main_uptrend']:.2%}, "
            f"W/L={wl:.2f} -> **P0 {verdict}** (threshold > 2.5:1)"
        )
    lines.append("")

    lines.append("## 6. Risks")
    lines.append("")
    lines.append("- Sample 2019-2023 includes bull/bear transitions")
    lines.append("- Delisted stocks retained may cause look-ahead bias")
    lines.append("- OBV/ADX signals distorted in extreme conditions")
    lines.append("- Transaction costs and slippage not modeled")
    lines.append("")

    report_text = "\n".join(lines)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print(f"Report: {REPORT_PATH}")
    if default_row:
        print(f"Default: P={default_row['P_main_uptrend']:.2%}, W/L={default_row['win_loss_ratio']:.2f}")
    print("Done.")


if __name__ == "__main__":
    main()
