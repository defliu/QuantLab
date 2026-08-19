# Backtest Report -- trae_uptrend_v3b_full

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260818_235535_6c018a |
| run_started_at | 2026-08-18T23:55:35 |
| runtime_seconds | 824.57 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 184.32% |
| 年化(线性) | 25.59% |
| 年化(CAGR) | 15.61% |
| max_drawdown | -17.91% |
| sharpe | 0.903 |
| calmar | 1.429 |
| 卡玛(CAGR) | 0.872 |
| win_rate | 32.51% |
| n_trades | 1372 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600105.SH | 400 | 359.28 | 799.27 | 175996.19 |
| 002636.SZ | 1400 | 100.99 | 327.85 | 317607.09 |
| 301188.SZ | 5800 | 24.97 | 69.32 | 257265.05 |
| 600176.SH | 100 | 652.16 | 1423.57 | 77140.68 |
| 600888.SH | 800 | 127.43 | 224.37 | 77550.09 |
| 002317.SZ | 600 | 271.80 | 283.32 | 6910.29 |
| 605020.SH | 3300 | 50.15 | 59.97 | 32403.35 |
| 600869.SH | 500 | 314.61 | 365.17 | 25279.36 |
| 300304.SZ | 1000 | 158.31 | 155.07 | -3238.23 |
| 002937.SZ | 600 | 76.47 | 77.79 | 792.69 |
| 002057.SZ | 600 | 74.59 | 77.17 | 1545.88 |
| 301055.SZ | 900 | 48.88 | 45.02 | -3470.57 |

## 关键日志摘录

```
[ERROR] 2024-11-26 unfilled_order code=300131.SZ reason=suspended
[ERROR] 2024-11-27 unfilled_order code=300131.SZ reason=suspended
[ERROR] 2024-11-28 unfilled_order code=300131.SZ reason=suspended
[ERROR] 2024-12-24 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2024-12-27 unfilled_order code=600391.SH reason=limit_up_at_open
[ERROR] 2025-11-25 unfilled_order code=002271.SZ reason=below_min_lot
[ERROR] 2026-03-04 unfilled_order code=300438.SZ reason=below_min_lot
[ERROR] 2026-06-22 unfilled_order code=600761.SH reason=below_min_lot
[ERROR] 2026-06-24 unfilled_order code=600160.SH reason=below_min_lot
[ERROR] 2026-06-24 unfilled_order code=002056.SZ reason=below_min_lot
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
