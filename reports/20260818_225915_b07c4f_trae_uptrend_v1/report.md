# Backtest Report -- trae_uptrend_v1

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260818_225915_b07c4f |
| run_started_at | 2026-08-18T22:59:15 |
| runtime_seconds | 971.526 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 29.69% |
| 年化(线性) | 4.12% |
| 年化(CAGR) | 3.68% |
| max_drawdown | -39.01% |
| sharpe | 0.281 |
| calmar | 0.106 |
| 卡玛(CAGR) | 0.094 |
| win_rate | 40.14% |
| n_trades | 830 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600105.SH | 200 | 359.28 | 799.27 | 87998.10 |
| 002636.SZ | 900 | 100.99 | 327.85 | 204175.99 |
| 300199.SZ | 500 | 174.15 | 183.93 | 4889.27 |
| 002317.SZ | 300 | 271.80 | 283.32 | 3455.15 |
| 600012.SH | 1900 | 49.26 | 47.28 | -3764.50 |
| 600869.SH | 200 | 314.61 | 365.17 | 10111.74 |
| 002033.SZ | 1700 | 56.47 | 53.48 | -5073.24 |
| 600761.SH | 400 | 252.57 | 242.75 | -3924.76 |
| 600160.SH | 100 | 527.87 | 606.49 | 7862.77 |
| 002937.SZ | 1400 | 76.47 | 77.79 | 1849.61 |
| 603995.SH | 1600 | 41.86 | 40.63 | -1960.38 |
| 605007.SH | 5400 | 14.57 | 14.67 | 552.46 |

## 关键日志摘录

```
[ERROR] 2021-02-19 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-22 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-23 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-24 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-25 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2021-02-26 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2024-12-24 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2025-05-21 unfilled_order code=600795.SH reason=below_min_lot
[ERROR] 2025-07-04 unfilled_order code=002001.SZ reason=below_min_lot
[ERROR] 2026-01-07 unfilled_order code=000001.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-16T15:21:30
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-14, 5820 codes
- universe_coverage: 5433/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
