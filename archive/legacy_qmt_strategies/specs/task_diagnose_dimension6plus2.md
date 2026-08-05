# Task: Create diagnose_dimension6plus2.py — 6+2评分诊断脚本

## Goal
Create a script that loads stocks from the pool file, downloads OHLCV data via mootdx, runs ScoreCalculator6Plus2 scoring, and prints a sorted score table.

## Files

### Input
- Pool file: `D:/QMT_POOL/selected.txt` (tab-separated, 6-digit codes with optional .SH/.SZ suffix)

### Output
- **stdout**: ranked score table (stock_code, 8 dimension scores, total)
- **CSV file**: `D:/QMT_STRATEGIES/diagnose_6plus2_scores.csv` (same data)

### Reference
- Existing `scripts/diagnose_score_8d.py` — use its `read_pool()`, `download_data()` via mootdx, and `_strip_suffix()` as-is
- Scorer: `from core.scoring.dimension6plus2 import ScoreCalculator6Plus2`
- `ScoreCalculator6Plus2` constructor: `ScoreCalculator6Plus2(sector_heat_path="D:/QMT_POOL/sector_heat.json")`
- `ScoreCalculator6Plus2.score_single(stock_code, df, dynamic_pe=None, static_pe=None)` → dict with keys: score_breakout, score_trend, score_consolidation, score_volumeprice, score_macd, score_valuation, score_sentiment, score_sector, score_total
- For PE values: pass `dynamic_pe=20.0, static_pe=25.0` as reasonable defaults since we don't have real fundamentals

### Logic
1. Read pool file, get 6-digit codes
2. For each code, add .SH or .SZ suffix based on exchange prefix (6xxxxx→SH, 0/3xxxxx→SZ)
3. Download ~150 bars of daily data via mootdx (REQ_BARS=150)
4. For each stock with enough data (>= 30 bars), create DataFrame with columns: open, high, low, close, volume
5. Run `scorer.score_single(code_with_suffix, df, dynamic_pe=20.0, static_pe=25.0)`
6. Collect all scores, sort by score_total descending
7. Print table to stdout and save CSV

### Table format (stdout)
```
Rank | Code   | Brk(22) | Trd(13) | Con(20) | Vol(12) | MAC(12) | Val(7) | Sen(7) | Sec(7) | Total(100)
-----+--------+---------+---------+---------+---------+---------+--------+--------+--------+-----------
  1  | 000001 |  15.0   |  13.0   |  20.0   |  12.0   |  12.0   |  3.5   |  3.5   |  3.5   |   82.5
```

### CSV columns (header required)
stock_code,score_breakout,score_trend,score_consolidation,score_volumeprice,score_macd,score_valuation,score_sentiment,score_sector,score_total

### Location
Save to: `D:/QMT_STRATEGIES/scripts/diagnose_dimension6plus2.py`

## Testing & verification
1. Script must run without QMT/xtquant — only uses mootdx (通达信TCP) for data
2. Run: `cd D:/QMT_STRATEGIES && C:/Users/Administrator/AppData/Local/Programs/Python/Python310/python.exe scripts/diagnose_dimension6plus2.py`
3. Must print at least 3 stocks with valid scores (no crash on missing data)
4. All scores must be finite numbers between 0 and their dimension max

## Constraints
- UTF-8 encoding
- Python 3.10 compatible
- Handle pool file read errors gracefully (empty pool → print warning, not crash)
- Handle mootdx download failures per-stock (skip, print "SKIP: {code} {reason}")
- Timeout mootdx per stock at 5 seconds

DO NOT start coding until I say "EXECUTE". This is the spec.
