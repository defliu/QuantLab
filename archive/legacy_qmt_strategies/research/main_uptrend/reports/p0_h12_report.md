# P0 H12 main uptrend conditional probability report

**Generated**: 2026-06-19 16:27:11

## 1. Data

- DB: E:/huicexitong/runtime/sj/gpsj.duckdb
- Table: '日线数据' (117 cols)
- Period: 2019-01-01 ~ 2023-12-31
- Stocks: 7180
- Rows: 6,439,153
- Candidates (score>=60): 454,106

## 2. Indicators

7-factor trend strength score:
- F1: ADX(14) clamp[20,40] linear -> x0.20
- F2: MA5 slope 252d rolling percentile -> x0.15
- F3: (MA5-MA20)/MA20 252d rolling percentile -> x0.15
- F4: RSI(14) >50 duration (20d) -> x0.10
- F5: Price position 52W; >0.8 -20pt; >0.9 invalid -> x0.10
- F6: ATR compression+vol breakout -> x0.15
- F7: OBV divergence -> x0.15

Label: T0+60d max gain >= rise_th AND max drawdown <= dd_th

## 3. 9-group sensitivity

| rise_th | dd_th | N_candidate | P(main) | fake_breakout | avg_gain | avg_loss | W/L ratio |
|---------|-------|-------------|---------|---------------|----------|----------|-----------|
| 0.25 | 0.10 | 454,106 | 1.27% | 98.73% | 47.97% | -6.34% | 7.56 |
| 0.25 | 0.12 | 454,106 | 2.55% | 97.45% | 48.44% | -6.50% | 7.45 |
| 0.25 | 0.15 | 454,106 | 5.17% | 94.83% | 49.74% | -6.78% | 7.34 |
| 0.30 | 0.10 | 454,106 | 0.95% | 99.05% | 54.91% | -5.73% | 9.59 |
| 0.30 | 0.12 | 454,106 | 1.93% | 98.07% | 55.21% | -5.85% | 9.43 |
| 0.30 | 0.15 | 454,106 | 3.96% | 96.04% | 56.62% | -6.06% | 9.34 |
| 0.35 | 0.10 | 454,106 | 0.73% | 99.27% | 61.74% | -5.23% | 11.80 |
| 0.35 | 0.12 | 454,106 | 1.49% | 98.51% | 61.96% | -5.33% | 11.62 |
| 0.35 | 0.15 | 454,106 | 3.11% | 96.89% | 63.26% | -5.49% | 11.51 |

## 4. Default group (30%/12%) by year

| year | N_candidate | N_positive | P(main) |
|------|-------------|------------|---------|
| 2020 | 90,243 | 2,399 | 2.66% |
| 2021 | 116,395 | 2,369 | 2.04% |
| 2022 | 114,417 | 2,199 | 1.92% |
| 2023 | 133,051 | 1,801 | 1.35% |

## 5. Conclusion

Default (rise=30%, dd=12%): P=1.93%, W/L=9.43 -> **P0 PASS** (threshold > 2.5:1)

## 6. Risks

- Sample 2019-2023 includes bull/bear transitions
- Delisted stocks retained may cause look-ahead bias
- OBV/ADX signals distorted in extreme conditions
- Transaction costs and slippage not modeled
