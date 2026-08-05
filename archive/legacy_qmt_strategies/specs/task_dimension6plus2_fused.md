# Task Spec: 6+2 Fused Scoring Module (Dimension6Plus2)

## 1. Overview
Implement a new momentum-based scoring module `core/scoring/dimension6plus2.py` that fuses the DEEPSEEK 6-dimension framework (breakout-centric, with "dense trading area" filtering) with Sentiment and Sector Heat from the legacy 8D system.

**Philosophy**: 6 dimensions form the skeleton (86 points). Sentiment + Sector are environment amplifiers (14 points). The module is pure calculation logic with zero QMT/xtquant dependency.

## 2. File Location
- **Target**: `D:/QMT_STRATEGIES/core/scoring/dimension6plus2.py`
- **Tests**: `D:/QMT_STRATEGIES/tests/test_dimension6plus2.py`
- **Existing utilities to reuse**: `core/utils.py` (ema, ma, calc_macd, safe_last, calc_bias)
- **Reference**: `core/signal_main_rise.py` (sector_heat JSON loading pattern only)

## 3. Scoring Framework (Locked)

| # | Dimension | Weight | Core Logic |
|---|-----------|--------|------------|
| 1 | **Breakout Validity** | 22 | DEEPSEEK dense-area logic + volume confirmation |
| 2 | **Trend Health** | 13 | DEEPSEEK wide zone (0%~12% bias = full marks) |
| 3 | **Consolidation Strength** | 20 | DEEPSEEK 5-day window, MA5 x 0.998 threshold |
| 4 | **Volume-Price Health** | 12 | DEEPSEEK discrete + continuous hybrid |
| 5 | **MACD Momentum** | 12 | DEEPSEEK binary base + partial credit fallback |
| 6 | **Valuation Safety** | 7 | Compressed weight, 519-pool friendly |
| 7 | **Sentiment** | 7 | 5-day return percentile within pool |
| 8 | **Sector Heat** | 7 | sector_heat.json linear mapping |
| | **Total** | **100** | |

---

### Dimension 1: Breakout Validity (22分)

**Prerequisite**: Compute `resistance = df['high'].rolling(20).max().shift(1)` (20-day high excluding today). Compute `amplitude_20 = df['high'].rolling(20).max() / df['low'].rolling(20).min() - 1`.

**Dense Area Check**:
- `is_dense = amplitude_20.iloc[-1] <= 0.15`

**Breakout Check**:
- `breakout_pct = (close - resistance.iloc[-1]) / resistance.iloc[-1] * 100`
- `is_breakout = breakout_pct > 0`

**Volume Confirmation**:
- `vol_ratio = df['volume'].iloc[-1] / df['volume'].rolling(5).mean().iloc[-1]`

**Scoring**:
```
if is_breakout and is_dense:
    base = 15
elif is_breakout and not is_dense:
    base = 8
else:
    base = 0

if vol_ratio >= 2.0:
    vol_bonus = 7
elif vol_ratio >= 1.2:
    vol_bonus = 4
else:
    vol_bonus = 0

score = min(base + vol_bonus, 22)
```

> Rationale: DEEPSEEK's "dense area" filter (amplitude <= 15%) is the core semantic of 519 breakout stock selection. Volume bonus rewards confirmation but caps at 22.

---

### Dimension 2: Trend Health (13分)

**Compute**: `bias_5 = calc_bias(close, ma5)` using `core.utils.calc_bias`.

**Scoring**:
```
if 0 < bias_5 <= 12:
    score = 13
elif 12 < bias_5 <= 15:
    score = 7
else:
    score = 0
```

> Rationale: DEEPSEEK's wide optimal zone (0~12% = full marks) is more forgiving for momentum stocks than my earlier narrow zone. 13分 compressed from 15 to make room for +2 dimensions.

---

### Dimension 3: Consolidation Strength (20分)

**Compute**: For each of the last 5 trading days, check if `low >= ma5 * 0.998` (where ma5 is computed per-day, not a single value). Count `hold_days` where condition holds.

**Scoring**:
```
if hold_days == 5:
    score = 20
elif hold_days == 4:
    score = 15
elif hold_days == 3:
    score = 10
else:
    score = 0
```

> Rationale: DEEPSEEK's 5-day window with 0.998 threshold captures the "gentle pullback, price riding MA5" pattern typical of 主升浪启动. 20分 is the highest weight besides breakout, reflecting its importance.

---

### Dimension 4: Volume-Price Health (12分)

**Compute**: `vol_ratio = today_volume / vol_ma5`.

**Scoring** (hybrid: DEEPSEEK discrete + continuous):
```
if vol_ratio <= 1.5:
    score = 12
elif vol_ratio <= 2.5:
    # linear decay from 12 to 0
    score = 12 * (2.5 - vol_ratio) / 1.0
else:
    score = 0
```

> Rationale: DEEPSEEK's strict cutoff (>2.0 = 0) is relaxed slightly to a continuous ramp (>2.5 = 0), giving partial credit for moderately elevated volume. 12分 compressed from 15.

---

### Dimension 5: MACD Momentum (12分)

**Compute**: Use `calc_macd(close)` from `core.utils` → `diff, dea, macd`. `macd_today = macd.iloc[-1]`, `macd_yesterday = macd.iloc[-2]`.

**Scoring** (binary base + partial credit):
```
if macd_today >= macd_yesterday:
    score = 12
else:
    # Partial credit based on how much momentum is lost
    # If both positive but shrinking: 6
    # If positive to negative: 3
    # If both negative and deepening: 0
    if macd_today > 0 and macd_yesterday > 0:
        score = 6
    elif macd_today > 0 and macd_yesterday <= 0:
        score = 9  # crossed above, bonus
    elif macd_today <= 0 and macd_yesterday > 0:
        score = 3
    else:
        score = 0
```

> Rationale: DEEPSEEK's pure binary (>= yesterday = 15, else 0) is too harsh for noisy intraday data. Adding partial credit (especially for "positive but shrinking" = 6) improves robustness. 12分 compressed from 15.

---

### Dimension 6: Valuation Safety (7分)

**Inputs**: `dynamic_pe`, `static_pe` (from fundamentals dict or DataFrame columns).

**Scoring**:
```
if dynamic_pe is None or static_pe is None or dynamic_pe <= 0 or static_pe <= 0:
    score = 3.5
elif dynamic_pe <= static_pe:
    score = 7.0
else:
    # Continuous penalty: the closer dynamic_pe is to static_pe, the more partial credit
    ratio = static_pe / dynamic_pe
    score = 7.0 * max(0, ratio)
```

> Rationale: Weight compressed from 15 to 7 to make room for Sentiment/Sector. Continuous fallback (instead of DEEPSEEK's strict 0) is 519-pool friendly — high-PE growth stocks still get partial credit if their dynamic PE isn't catastrophically above static PE.

---

### Dimension 7: Sentiment (7分)

**Compute cross-sectionally** within the pool:
1. For each stock, compute `ret_5d = close.iloc[-1] / close.iloc[-6] - 1` (5-day return).
2. Rank all stocks by `ret_5d` descending. `rank` 0 = best.
3. `pctile = (N - 1 - rank) / (N - 1) * 100` (0 = worst, 100 = best).

**Scoring**:
```
if N == 1:
    score = 7.0
elif pctile >= 80:
    score = 7.0
elif pctile <= 20:
    score = 0.0
else:
    score = 7.0 * (pctile - 20) / 60
```

> Rationale: Top 20% of the pool in 5-day momentum gets full marks. Bottom 20% gets zero. Linear interpolation in between.

---

### Dimension 8: Sector Heat (7分)

**Load**: `sector_heat.json` using same pattern as `ScoreCalculator8D.load_sector_heat_from_file`.

Expected JSON formats:
```json
{"stock_heat": {"000001": 85.5, "000002": 42.0}}
```
or plain:
```json
{"000001": 85.5, "000002": 42.0}
```

**Scoring**:
```
heat = heat_map.get(stock_code_clean, None)
if heat is None:
    score = 3.5
else:
    score = 7.0 * max(0, min(100, heat)) / 100
```

> Rationale: Linear mapping from 0-100 heat score to 0-7 points. Missing data defaults to neutral (3.5).

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
                          If missing, valuation defaults to 3.5.

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

## 5. Implementation Requirements

1. **All 6 core dimensions are computed independently per stock** in the first pass.
2. **Sentiment is computed in a second pass** after all 5-day returns are collected.
3. **Sector heat is loaded once in `__init__`** and looked up per stock.
4. **Stock code normalization**: Handle both `"000001.SZ"` and `"000001"` formats when looking up sector heat.
5. **Minimum bars**: If a DataFrame has fewer than 30 rows, still attempt scoring but print a warning. For dimensions requiring N days (e.g., 5-day consolidation), use available data and scale proportionally where applicable.
6. **Deterministic**: Same input → same output. No randomness.

## 6. Input / Output Examples

### Example Input
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

### Example Output
```
              score_breakout  score_trend  score_consolidation  ...  score_sector  score_total
000001.SZ              22.00        13.00                20.00  ...           5.60        95.30
000002.SZ               8.00        13.00                15.00  ...           3.50        52.20
```

## 7. Test Plan

Write `tests/test_dimension6plus2.py` using existing `conftest.py` fixtures.

1. **test_breakout_dense_area**: Mock DF where 20-day amplitude = 10% (dense) + close breaks 20-day high + vol_ratio=1.8 → expect 15+4=19.
2. **test_breakout_non_dense**: Same but 20-day amplitude = 20% → expect 8+4=12.
3. **test_breakout_no_break**: Close below 20-day high → expect 0 regardless of volume.
4. **test_trend_health_zones**: bias=5% → 13; bias=13% → 7; bias=20% → 0; bias=-1% → 0.
5. **test_consolidation_perfect**: 5 days all low >= ma5*0.998 → 20; 4 days → 15; 2 days → 0.
6. **test_volume_price_discrete**: vol_ratio=1.2 → 12; vol_ratio=2.0 → 6; vol_ratio=3.0 → 0.
7. **test_macd_binary_and_partial**: macd_today >= yesterday → 12; both positive but shrinking → 6; positive-to-negative → 3; both negative deepening → 0.
8. **test_valuation_safety**: dynamic=10, static=15 → 7; dynamic=40, static=20 → 3.5; missing → 3.5.
9. **test_sentiment_cross_sectional**: 5-stock pool with known returns. Top → 7, bottom → 0, middle → interpolated.
10. **test_sentiment_single_default**: `score_single` without `pool_5d_returns` → sentiment=3.5.
11. **test_sector_heat_missing**: Nonexistent JSON path → all sector scores = 3.5, no exception.
12. **test_total_max**: Construct ideal DF (dense breakout + optimal bias + 5-day consolidation + healthy volume + positive MACD momentum + dynamic<=static + top sentiment + top sector) → total should be near 100.
13. **test_empty_pool**: `score_pool({})` returns empty DataFrame with correct columns.
14. **test_short_df**: 15-row DF should not crash; consolidation scaled proportionally.

## 8. Acceptance Criteria
- [ ] All 14 pytest cases pass (`pytest tests/test_dimension6plus2.py -v`).
- [ ] No Chinese variable names in `dimension6plus2.py` (comments in Chinese OK, variable names English only).
- [ ] No hardcoded absolute paths except the default `sector_heat_path` in `__init__`.
- [ ] No dependency on QMT, xtquant, or `ContextInfo`. Pure pandas/numpy.
- [ ] `score_pool` and `score_single` are deterministic.
- [ ] Compatible with existing `core/utils.py` imports.

## 9. Historical Reference
- DEEPSEEK 6D specification: `D:/QMTTDX/主升浪策略/01_策略文档/6维打分DEEPSEEK版.md`
- Legacy 8D scoring: `core/signal_main_rise.py` class `ScoreCalculator8D`
- Sector heat JSON format: `{"stock_heat": {"000001": 85.5, ...}}` or plain `{"000001": 85.5, ...}`
- Existing test fixtures: `tests/conftest.py`
- Existing utils: `core/utils.py`
