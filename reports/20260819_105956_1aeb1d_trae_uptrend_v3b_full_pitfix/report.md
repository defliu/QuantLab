# Backtest Report -- trae_uptrend_v3b_full_pitfix

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260819_105956_1aeb1d |
| run_started_at | 2026-08-19T10:59:56 |
| runtime_seconds | 854.366 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 102.14% |
| 年化(线性) | 14.18% |
| 年化(CAGR) | 10.26% |
| max_drawdown | -20.11% |
| sharpe | 0.638 |
| calmar | 0.705 |
| 卡玛(CAGR) | 0.510 |
| win_rate | 30.74% |
| n_trades | 1427 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600105.SH | 400 | 359.28 | 799.27 | 175996.19 |
| 301188.SZ | 3200 | 24.97 | 69.32 | 141939.34 |
| 600176.SH | 100 | 625.29 | 1423.57 | 79827.19 |
| 002317.SZ | 300 | 271.80 | 283.32 | 3455.15 |
| 605020.SH | 1000 | 49.47 | 59.97 | 10497.49 |
| 600869.SH | 400 | 314.61 | 365.17 | 20223.49 |
| 600160.SH | 100 | 527.87 | 606.49 | 7862.77 |
| 002056.SZ | 200 | 294.29 | 310.20 | 3183.24 |
| 002937.SZ | 1400 | 76.47 | 77.79 | 1849.61 |
| 002057.SZ | 1400 | 74.59 | 77.17 | 3607.05 |
| 301055.SZ | 2200 | 48.88 | 45.02 | -8483.62 |

## 关键日志摘录

```
[ERROR] 2021-02-26 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2021-04-21 unfilled_order code=600606.SH reason=below_min_lot
[ERROR] 2023-01-09 unfilled_order code=600477.SH reason=below_min_lot
[ERROR] 2023-06-13 unfilled_order code=600660.SH reason=below_min_lot
[ERROR] 2024-09-30 unfilled_order code=002970.SZ reason=limit_up_at_open
[ERROR] 2024-10-08 unfilled_order code=603337.SH reason=limit_up_at_open
[ERROR] 2024-12-27 unfilled_order code=600391.SH reason=limit_up_at_open
[ERROR] 2025-05-21 unfilled_order code=600795.SH reason=below_min_lot
[ERROR] 2025-11-25 unfilled_order code=002271.SZ reason=below_min_lot
[ERROR] 2026-03-04 unfilled_order code=300438.SZ reason=below_min_lot
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
