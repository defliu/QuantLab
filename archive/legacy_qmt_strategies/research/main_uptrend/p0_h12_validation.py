# -*- coding: utf-8 -*-
"""P0 H12 validation script - main uptrend conditional probability.

Two-stage pipeline:
- Stage A: load + adjust + trend score -> cache/scored.parquet (~30 min)
- Stage B: sensitivity + yearly + report (reads cache, ~30-60 min after pre-index)

Run:
    python p0_h12_validation.py --stage a
    python p0_h12_validation.py --stage b
    python p0_h12_validation.py            # back-compat: a + b
"""
import argparse
import datetime as dt
import json
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

# ============================================================
# Paths & constants
# ============================================================
DB_PATH = "E:/huicexitong/runtime/sj/gpsj.duckdb"
ROOT = Path("D:/QMT_STRATEGIES/research/main_uptrend")
CACHE_DIR = ROOT / "cache"
CACHE_PARQUET = CACHE_DIR / "scored.parquet"
CACHE_META = CACHE_DIR / "meta.json"
REPORT_PATH = ROOT / "reports" / "p0_h12_report.md"
STATS_CSV = ROOT / "reports" / "p0_h12_statistics.csv"
YEARLY_CSV = ROOT / "reports" / "p0_h12_yearly.csv"

RISE_THRESHOLDS = [0.25, 0.30, 0.35]
DRAWDOWN_THRESHOLDS = [0.10, 0.12, 0.15]
WINDOW_DAYS = 60


# ============================================================
# 1. Data access layer
# ============================================================

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
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = arr[i - window + 1:i + 1]
        cur = arr[i]
        if np.isnan(cur):
            continue
        valid = w[~np.isnan(w)]
        if len(valid) == 0:
            continue
        out[i] = (valid < cur).sum() / len(valid) * 100.0
    return out


def _np_roll_mean(arr, window):
    n = len(arr)
    out = np.full(n, np.nan)
    cs = np.zeros(n + 1)
    mask = ~np.isnan(arr)
    cs[1:] = np.cumsum(np.where(mask, arr, 0.0))
    cnt = np.zeros(n + 1)
    cnt[1:] = np.cumsum(mask.astype(float))
    for i in range(window - 1, n):
        if cnt[i + 1] - cnt[i + 1 - window] == window:
            out[i] = (cs[i + 1] - cs[i + 1 - window]) / window
    return out


def _np_roll_max(arr, window):
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        seg = arr[i - window + 1:i + 1]
        if not np.any(np.isnan(seg)):
            out[i] = np.max(seg)
    return out


def _np_roll_min(arr, window):
    n = len(arr)
    out = np.full(n, np.nan)
    for i in range(window - 1, n):
        seg = arr[i - window + 1:i + 1]
        if not np.any(np.isnan(seg)):
            out[i] = np.min(seg)
    return out


def _linear_slope_rolling(arr, window=5):
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

    adx = _adx(high, low, close, 14)
    f1 = np.clip((adx - 20.0) / 20.0, 0.0, 1.0) * 100.0

    ma5 = _np_roll_mean(close, 5)
    slope5 = _linear_slope_rolling(ma5, 5)
    f2 = _rolling_percentile(slope5, 252)

    ma20 = _np_roll_mean(close, 20)
    divergence = (ma5 - ma20) / np.where(ma20 == 0, 1e-10, ma20)
    f3 = _rolling_percentile(divergence, 252)

    rsi = _rsi(close, 14)
    rsi_above50 = (rsi > 50.0).astype(float)
    f4 = _np_roll_mean(rsi_above50, 20) * 100.0

    hi52 = _np_roll_max(high, 252)
    lo52 = _np_roll_min(low, 252)
    rng52 = hi52 - lo52
    f5 = np.where(rng52 > 0, (close - lo52) / rng52 * 100.0, 50.0)
    f5[:251] = np.nan

    atr14 = _atr(high, low, close, 14)
    atr60_mean = _np_roll_mean(atr14, 60)
    vol20_mean = _np_roll_mean(vol, 20)
    ratio_atr = np.where(atr60_mean > 0, atr14 / atr60_mean, 1.0)
    ratio_vol = np.where(vol20_mean > 0, vol / vol20_mean, 1.0)
    f6 = np.where(
        (ratio_atr <= 0.8) & (ratio_vol > 1.3), 100.0,
        np.where(ratio_atr > 0, np.clip(100.0 * (1.0 - ratio_atr), 0.0, 100.0), 0.0)
    )
    f6[:59] = np.nan

    sign = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(sign * vol)
    price_20high = _np_roll_max(close, 20)
    obv_20high = _np_roll_max(obv, 20)
    f7 = np.where(
        (close >= price_20high) & (obv < obv_20high), 85.0, 100.0
    )
    f7[:19] = np.nan

    weights = np.array([0.20, 0.15, 0.15, 0.10, 0.10, 0.15, 0.15])
    raw = np.column_stack([f1, f2, f3, f4, f5, f6, f7])
    clamped = np.clip(raw, 0.0, 100.0)
    total = np.nansum(clamped * weights, axis=1)
    any_nan = np.any(np.isnan(raw), axis=1)
    total[any_nan] = np.nan

    f5_pct = f5 / 100.0
    total[f5_pct > 0.9] = np.nan
    mask_80 = (f5_pct > 0.8) & (f5_pct <= 0.9)
    total[mask_80] -= 20.0

    return pd.Series(total, index=df.index)


# ============================================================
# 4. Stage A: load + adjust + trend score -> parquet
# ============================================================

def stage_a():
    print("=" * 60)
    print("Stage A: load + adjust + trend score")
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

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df_cache = df[["code", "date", "adj_close", "trend_score"]].copy()
    df_cache.to_parquet(CACHE_PARQUET, index=False)

    n_candidates = int((df_cache["trend_score"] >= 60.0).sum())
    meta = {
        "table_raw": table_raw,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "n_rows": int(len(df_cache)),
        "n_codes": int(n_codes),
        "n_candidates": n_candidates,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    CACHE_META.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nCache:  {CACHE_PARQUET}")
    print(f"Meta:   {CACHE_META}")
    print(f"  Rows: {len(df_cache):,}")
    print(f"  Candidates (score>=60): {n_candidates:,}")
    print("Stage A done.")


# ============================================================
# 5. Stage B helpers
# ============================================================

def _build_code_index(adj_close_all, code_arr, idx_arr, unique_codes):
    """Pre-build {code -> stock_close_array, code -> {global_idx -> local_pos}}.

    This eliminates the per-group full-table mask scan in the original code
    (was 9x O(N) for sensitivity + 1x for yearly). Now built once, reused.
    """
    code_to_close = {}
    code_to_pos = {}
    for ucode in unique_codes:
        stock_mask = code_arr == ucode
        stock_indices = idx_arr[stock_mask]
        code_to_close[ucode] = adj_close_all[stock_mask]
        code_to_pos[ucode] = {v: k for k, v in enumerate(stock_indices)}
    return code_to_close, code_to_pos


def _max_drawdown(future, base_price):
    peak = base_price
    max_dd = 0.0
    for v in future:
        if v > peak:
            peak = v
        dd = 1.0 - v / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _eval_one_group(rise_th, dd_th, cand_indices, cand_codes,
                    unique_cand_codes, code_to_close, code_to_pos):
    """Run one (rise_th, dd_th) cell. Returns (gains_list, losses_list)."""
    gains_list = []
    losses_list = []
    for ccode in unique_cand_codes:
        c_mask = cand_codes == ccode
        c_indices = cand_indices[c_mask]
        stock_close = code_to_close[ccode]
        idx_to_pos = code_to_pos[ccode]
        for ci in c_indices:
            if ci not in idx_to_pos:
                continue
            local_i = idx_to_pos[ci]
            end = min(local_i + WINDOW_DAYS + 1, len(stock_close))
            future = stock_close[local_i + 1:end]
            if len(future) < 10:
                continue
            base = stock_close[local_i]
            max_gain = np.max(future) / base - 1.0
            if max_gain < rise_th:
                continue
            max_dd = _max_drawdown(future, base)
            if max_dd <= dd_th:
                gains_list.append(max_gain)
            else:
                losses_list.append(np.min(future) / base - 1.0)
    return gains_list, losses_list


# ============================================================
# 6. Stage B: sensitivity + yearly + report
# ============================================================

def stage_b():
    print("=" * 60)
    print("Stage B: sensitivity + yearly + report")
    print("=" * 60)

    if not CACHE_PARQUET.exists() or not CACHE_META.exists():
        raise RuntimeError(
            f"Cache missing. Run --stage a first.\n"
            f"  Expected: {CACHE_PARQUET}\n"
            f"  Expected: {CACHE_META}"
        )

    print(f"Loading cache: {CACHE_PARQUET}")
    df = pd.read_parquet(CACHE_PARQUET).reset_index(drop=True)
    meta = json.loads(CACHE_META.read_text(encoding="utf-8"))
    print(f"  Rows: {len(df):,}")
    print(f"  Meta: {meta}")

    candidates = df[df["trend_score"] >= 60.0].copy()
    print(f"\nCandidates (score>=60): {len(candidates):,}")

    adj_close_all = df["adj_close"].values.astype(float)
    code_arr = df["code"].values
    idx_arr = df.index.values

    cand_indices = candidates.index.values
    cand_codes = candidates["code"].values
    unique_cand_codes = np.unique(cand_codes)

    print(f"Pre-building per-code index for {len(unique_cand_codes)} codes ...")
    t_pre = time.time()
    code_to_close, code_to_pos = _build_code_index(
        adj_close_all, code_arr, idx_arr, unique_cand_codes
    )
    print(f"  Done in {time.time() - t_pre:.1f}s")

    print("\n--- 9-group sensitivity ---")
    results = []
    default_row = None
    default_gains = None
    default_losses = None
    for rise_th in RISE_THRESHOLDS:
        for dd_th in DRAWDOWN_THRESHOLDS:
            print(f"  rise={rise_th}, dd={dd_th} ...", end=" ", flush=True)
            t1 = time.time()
            gains_list, losses_list = _eval_one_group(
                rise_th, dd_th, cand_indices, cand_codes,
                unique_cand_codes, code_to_close, code_to_pos
            )
            n_cand = len(cand_indices)
            n_pos = len(gains_list)
            p_val = n_pos / n_cand if n_cand > 0 else 0.0
            avg_gain = float(np.mean(gains_list)) if gains_list else 0.0
            avg_loss = float(np.mean(losses_list)) if losses_list else 0.0
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
                default_gains = gains_list
                default_losses = losses_list
            print(f"N={n_cand:,}, P={p_val:.2%}, W/L={wl:.2f} ({time.time()-t1:.1f}s)")

    # Yearly breakdown for default group (reuse default_gains/losses count? No — need per-row labels)
    print("\nDefault group (30%/12%) yearly ...")
    default_labels = np.zeros(len(cand_indices), dtype=int)
    for i, ci in enumerate(cand_indices):
        ccode = cand_codes[i]
        idx_to_pos = code_to_pos[ccode]
        if ci not in idx_to_pos:
            continue
        local_i = idx_to_pos[ci]
        stock_close = code_to_close[ccode]
        end = min(local_i + WINDOW_DAYS + 1, len(stock_close))
        future = stock_close[local_i + 1:end]
        if len(future) < 10:
            continue
        base = stock_close[local_i]
        max_gain = np.max(future) / base - 1.0
        if max_gain < 0.30:
            continue
        if _max_drawdown(future, base) <= 0.12:
            default_labels[i] = 1

    candidates_copy = candidates.copy()
    candidates_copy["label"] = default_labels
    candidates_copy["year"] = pd.to_datetime(candidates_copy["date"]).dt.year
    yearly = candidates_copy.groupby("year").agg(
        N_candidate=("label", "count"),
        N_positive=("label", "sum"),
    ).reset_index()
    yearly["P"] = (yearly["N_positive"] / yearly["N_candidate"]).round(4)

    # Report
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
    lines.append(f"- Table: {meta['table_raw']!r} (117 cols)")
    lines.append(f"- Period: {meta['start_date']} ~ {meta['end_date']}")
    lines.append(f"- Stocks: {meta['n_codes']}")
    lines.append(f"- Rows: {meta['n_rows']:,}")
    lines.append(f"- Candidates (score>=60): {meta['n_candidates']:,}")
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
        lines.append(
            f"| {int(row['year'])} | {int(row['N_candidate']):,} | "
            f"{int(row['N_positive']):,} | {row['P']:.2%} |"
        )
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

    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    pd.DataFrame(results).to_csv(STATS_CSV, index=False, encoding="utf-8")
    yearly.to_csv(YEARLY_CSV, index=False, encoding="utf-8")

    print(f"Report: {REPORT_PATH}")
    print(f"Stats:  {STATS_CSV}")
    print(f"Yearly: {YEARLY_CSV}")
    if default_row:
        print(f"Default: P={default_row['P_main_uptrend']:.2%}, W/L={default_row['win_loss_ratio']:.2f}")
    print("Stage B done.")


# ============================================================
# 7. Entry
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["a", "b", "all"], default="all",
                        help="a: load+score; b: sensitivity+report; all: both (default)")
    args = parser.parse_args()
    if args.stage in ("a", "all"):
        stage_a()
    if args.stage in ("b", "all"):
        stage_b()
    print("Done.")


if __name__ == "__main__":
    main()
