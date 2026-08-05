# Task: Full Backtest for 6+2 Scorer with Sell Risk Management

## Objective
Create a complete backtest script for ScoreCalculator6Plus2 that integrates the full sell risk management engine from `core/risk_manager.py` (SellStrategyEngine).

## Background
The current `scripts/backtest_dimension6plus2.py` is a minimal backtest that buys top-3 daily and sells after 1 day at close — NO sell risk management at all. The existing `scripts/run_backtest.py` has full sell logic but uses ScoreCalculator8D (the old scorer). We need the 6+2 scorer with the same sell logic.

## Approach
Create a new script `scripts/backtest_6plus2_full.py` that:
1. Uses the same data pipeline as `scripts/backtest_dimension6plus2.py` (mootdx data download, rolling daily scoring)
2. Uses **ScoreCalculator6Plus2** for daily stock scoring (not 8D)
3. Integrates **SellStrategyEngine** from `core/risk_manager.py` for all sell decisions (not just 1-day hold)
4. Uses the sell parameters from `config/global_config.yaml`

## Sell Logic (from risk_manager.py)

The SellStrategyEngine implements four layers:
1. **Bottom line**: -5% loss / -7% daily drop → clear position
2. **Warning**: position reduced by 30-50% on signals (volume ratio >1.5, volume-price divergence, MACD shortening, MA5 slope flattening)
3. **Confirmation**: if warning persists → clear 50%
4. **Liquidation**: MA20 below for 3+ days → full clear
5. **Trailing stop**: 3 tiers (6%/8%/10% drawdown from high), MA5 break with 10% profit, chandelier ATR

## Input/Output

### Input config (from global_config.yaml):
```yaml
strategy:
  max_hold: 5
  target_ratio: 0.16
  hard_stop_loss: -0.08
sell:
  bottom_line_loss_pct: -0.05
  bottom_line_daily_drop_pct: -0.07
  warning_reduce_pct: 0.30
  warning_add_reduce_pct: 0.20
  volume_ratio_threshold: 1.5
  volume_diverge_threshold: 0.70
  macd_shorten_days: 3
  ma5_slope_flat_deg: 15
  confirm_reduce_pct: 0.50
  clear_ma20_days: 3
  trailing_break_ma5_interval: 0.10
  trailing_drawdown_lo: 0.06
  trailing_drawdown_mid: 0.08
  trailing_drawdown_hi: 0.10
  chandelier_atr_multiple: 3
  chandelier_min_lookback: 20
  no_reentry_days: 20
```

### CLI parameters:
```
--days 60       # backtest trading days (default 60)
--top 3         # top N stocks to buy daily (default 3)
--pool D:/QMT_POOL/selected.txt  # pool file
--capital 100000  # initial capital
--max-hold 5     # max concurrent holdings
```

### Output:
- Console: summary table (total trades, win rate, avg return, max drawdown, Sharpe)
- CSV: `data/backtest_6plus2_full_result.csv`
- JSON: `data/backtest_6plus2_full_report.json`

## Files to Create/Modify

### Create: `D:\QMT_STRATEGIES\scripts\backtest_6plus2_full.py`
Complete backtest script with:
1. Data module: read pool via `scripts/backtest_dimension6plus2.py` helpers, download via mootdx
2. Scoring module: daily rolling ScoreCalculator6Plus2 scoring
3. Trading module: buy top-N, manage positions, apply SellStrategyEngine sell decisions
4. Portfolio module: track P&L, compute metrics (win rate, Sharpe, max drawdown)
5. Report module: console table + CSV + JSON output

### Create: `D:\QMT_STRATEGIES\tests\test_backtest_6plus2_full.py`
Test the core logic:
1. Test position tracking: buy → price up → sell → correct P&L
2. Test sell engine integration: price drops 6% → bottom line triggered → clear
3. Test empty pool → graceful handling
4. Test output format: CSV has correct columns, JSON parseable

### DO NOT modify:
- `core/risk_manager.py`
- `core/scoring/dimension6plus2.py`
- `config/global_config.yaml`
- Any existing working tests

## Implementation Notes

1. **Data**: Use mootdx for daily OHLCV (same as `backtest_dimension6plus2.py`). No xtquant dependency.
2. **Rolling windows**: For each backtest day t, the DF available is df[:t+1] (no future data leak).
3. **SellStrategyEngine integration**:
   - Create a SellStrategyEngine instance per backtest run
   - On each day, for each held position, call `sell_engine.check_all()` with current data
   - Execute the returned SellDecision (CLEAR/REDUCE/HOLD)
4. **No future leaks**: bid/ask spread 0.1%, use next-day open for sell execution, current-day close for buy
5. **Portfolio metrics**: compute WinRate, AvgReturn, TotalReturn, MaxDrawdown, SharpeRatio

## Verification

```bash
# 1. Tests pass
C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_backtest_6plus2_full.py -v
# Expected: all tests pass

# 2. Full backtest runs
C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe scripts/backtest_6plus2_full.py --days 60 --top 3
# Expected: completes without error, produces CSV + JSON

# 3. No regression
C:\Users\Administrator\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v
# Expected: 0 new failures
```
