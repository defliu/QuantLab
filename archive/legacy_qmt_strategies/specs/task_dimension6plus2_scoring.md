# Task Spec: 6+2 Scoring Module (Dimension6Plus2)

## 1. Overview
Implement a new momentum-based scoring module `core/scoring/dimension6plus2.py` that fuses the KIM 6-dimension framework (breakout-centric) with Sentiment and Sector Heat from the legacy 8D system. This module is pure calculation logic with zero QMT/xtquant dependency.

## 2. File Location
- **Target**: `D:/QMT_STRATEGIES/core/scoring/dimension6plus2.py`
- **Tests**: `D:/QMT_STRATEGIES/tests/test_dimension6plus2.py`
- **Existing utilities to reuse**: `core/utils.py` (ema, ma, calc_macd, safe_last, calc_bias, etc.)
- **Reference implementation**: `core/signal_main_rise.py` (ScoreCalculator8D class — copy sector_heat loading pattern only)

## 3. Scoring Framework (Locked)

| Dimension | Weight | Formula (Deterministic) |
|-----------|--------|------------------------|
| **Breakout Validity** | 22 | `resistance = df['high'].rolling(20).max().shift(5)` (20-day high excluding last 5 days). `breakout_pct = (close - resistance) / resistance * 100`. Base score: linear map `breakout_pct` [0%, 5%] → [0, 15]. Volume confirmation: `vol_ratio = vol_ma5 / vol_ma10`. If `vol_ratio >= 1.5`: +7; `>= 1.2`: +4; else: `+min(4, vol_ratio * 3)`. Cap total at 22. If `breakout_pct < 0`: total = `max(0, vol_bonus * 0.3)`. |
| **Trend Health** | 18 | Compute `bias_5 = calc_bias(close, ma5)`. Optimal zone [2%, 6%] = 18. [0%, 2%]: linear 12→18. [6%, 10%]: linear 18→6. `< 0%` or `> 10%`: 0. Use `ma()` from utils. |
| **Consolidation Strength** | 13 | Last 10 days: count days where `low < ma5 * 0.99` (broke 1% below MA5). 0 days: 13; 1 day: 9; 2 days: 5; 3+ days: 0. If fewer than 10 bars, scale proportionally (`score * min(1, len/10)`). |
| **Volume-Price Health** | 13 | `vol_ratio = vol_ma5 / vol_ma10`. Healthy = moderate expansion without explosion. `score = 13 * (1 - abs(vol_ratio - 1.2) / 1.5)` clamped to [0, 13]. Additionally, if `vol_ratio > 3.0`: score = 0 (explosive volume penalty). If `close_ma5_pct > 5%` and `vol_ratio < 0.6`: score *= 0.5 (rising on dying volume). |
| **MACD Momentum** | 12 | Use `calc_macd(close)` from utils → `diff, dea, macd`. `macd_val = safe_last(macd)`, `macd_prev = macd.iloc[-2]`. If `macd_val > 0`: base = `8 + min(4, macd_val / 2.0)`. If `macd_val <= 0`: base = `max(0, 4 + macd_val / 2.0)`. Momentum bonus = `min(4, (macd_val - macd_prev) * 10.0)`. Total = base + bonus, clamp [0, 12]. |
| **Valuation Safety** | 8 | Inputs: `dynamic_pe`, `static_pe` (from DataFrame or passed dict). If either <= 0 or missing: 4.0. If `dynamic_pe <= static_pe`: 8.0. Else: `max(0, 8.0 * (static_pe / dynamic_pe))`. |
| **Sentiment** | 7 | Cross-sectional within pool. Compute `ret_5d = close / close.shift(5) - 1` for every stock in pool. Rank descending. `rank` 0 = best. For stock at rank `i` out of `N`: `pctile = (N - 1 - i) / (N - 1) * 100`. If `N == 1`: 7.0. If `pctile >= 80`: 7.0. If `pctile <= 50`: 0.0. Else: `7.0 * (pctile - 50) / 30`. |
| **Sector Heat** | 7 | Load `sector_heat.json` (same path pattern as `ScoreCalculator8D`). Map per-stock heat score (expected 0-100) to `7.0 * clamp(heat, 0, 100) / 100`. Missing = 3.5. |
| **Total** | **100** | Sum of above. Round to 2 decimals. |

## 4. Interface Definition

```python
class ScoreCalculator6Plus2:
    def __init__(self, sector_heat_path: str = 'D:/QMT_POOL/sector_heat.json'):
        """Load sector heat JSON. If missing, heat scores default to 3.5."""

    def score_pool(
        self,
        pool_dict: dict[str, pd.DataFrame],
        fundamentals: dict[str, dict] | None = None,
    ) -> pd.DataFrame:
        """
        Score an entire pool cross-sectionally.

        Args:
            pool_dict: {stock_code: DataFrame with columns [open, high, low, close, volume]}
                       Minimum 30 rows per DataFrame.
            fundamentals: Optional {stock_code: {'dynamic_pe': float, 'static_pe': float}}
                          If missing, valuation defaults to 4.0.

        Returns:
            DataFrame indexed by stock_code with columns:
            [score_breakout, score_trend, score_consolidation, score_volumeprice,
             score_macd, score_valuation, score_sentiment, score_sector,
             score_total]
        """

    def score_single(
        self,
        stock_code: str,
        df: pd.DataFrame,
        dynamic_pe: float | None = None,
        static_pe: float | None = None,
        pool_5d_returns: pd.Series | None = None,
    ) -> dict[str, float]:
        """
        Score a single stock. Used when pool scoring is not needed.
        If pool_5d_returns is provided, sentiment is computed from it;
        otherwise sentiment defaults to 3.5 (neutral).
        """
```

## 5. Input / Output Examples

### Example Input (pool_dict)
```python
pool_dict = {
    '000001.SZ': df1,  # 60 rows, columns: open/high/low/close/volume
    '000002.SZ': df2,
}
fundamentals = {
    '000001.SZ': {'dynamic_pe': 12.5, 'static_pe': 15.0},
    '000002.SZ': {'dynamic_pe': 28.0, 'static_pe': 20.0},
}
```

### Example Output (score_pool)
```
              score_breakout  score_trend  ...  score_sector  score_total
000001.SZ              18.50        16.20  ...           5.60        82.30
000002.SZ               8.00         9.00  ...           3.50        45.20
```

## 6. Test Plan

Write `tests/test_dimension6plus2.py` using the existing `conftest.py` fixtures (`mock_klines`, `mock_context`).

1. **test_breakout_validity**: Mock DataFrame with known 20-day high and volume. Assert breakout score increases when close > resistance and volume expands.
2. **test_trend_health_optimal**: Bias exactly 4% → score = 18. Bias -2% → score = 0.
3. **test_consolidation_strength**: Mock 10 days where low never breaks MA5 → 13. Break 2 days → 5.
4. **test_volume_price_explosion**: `vol_ratio = 4.0` → score = 0.
5. **test_macd_momentum**: Mock MACD series with positive/increasing values → high score. Negative/decreasing → low score.
6. **test_valuation_safety**: `dynamic_pe=10, static_pe=15` → 8. `dynamic_pe=40, static_pe=20` → 4.
7. **test_sentiment_cross_sectional**: Create 5-stock pool with known 5-day returns. Verify top stock gets 7, bottom gets 0, middle gets interpolated.
8. **test_sentiment_single_without_pool**: `score_single` without `pool_5d_returns` → sentiment = 3.5.
9. **test_sector_heat_missing_file**: Nonexistent path → all sector scores = 3.5, no exception.
10. **test_total_score_max**: Construct ideal DataFrame (strong breakout, optimal bias, no consolidation break, healthy volume, positive MACD momentum, dynamic<=static, top sentiment, top sector) → total should be 100.0.
11. **test_empty_pool**: `score_pool({})` returns empty DataFrame with correct columns.
12. **test_short_df_fallback**: DataFrame with 10 rows should not crash; scale consolidation accordingly.

## 7. Acceptance Criteria
- [ ] All 12 pytest cases pass (`pytest tests/test_dimension6plus2.py -v`).
- [ ] No Chinese variable names in `dimension6plus2.py` (comments in Chinese OK, variable names English only).
- [ ] No hardcoded absolute paths except the default `sector_heat_path` in `__init__`.
- [ ] No dependency on QMT, xtquant, or `ContextInfo`. Pure pandas/numpy.
- [ ] `score_pool` and `score_single` are deterministic (same input → same output, no randomness).
- [ ] Compatible with existing `core/utils.py` imports.

## 8. Historical Reference
- Legacy 8D scoring: `core/signal_main_rise.py` class `ScoreCalculator8D`
- Sector heat JSON format: `{"stock_heat": {"000001": 85.5, ...}}` or plain `{"000001": 85.5, ...}`
- Existing test fixtures: `tests/conftest.py`
- Existing utils: `core/utils.py`

## 9. Notes
- Use `safe_last()` from `core.utils` for extracting scalar values from Series.
- For `score_pool`, compute all 6 core dimensions first (independent per stock), then compute cross-sectional sentiment in a second pass, then assemble results.
- If a stock DataFrame has fewer than 30 rows, still attempt scoring but log a warning (print is acceptable for now).
- The module should be importable without any env vars or external config.
