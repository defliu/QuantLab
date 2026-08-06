# Backtest Report -- atr_lowvol_vt_fw

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260805_225201_55f564 |
| run_started_at | 2026-08-05T22:52:01 |
| runtime_seconds | 406.287 |
| config_hash | 1cb4757a6b866b9f58b66ee97c586d94f2f67e6e219d7e9cad99b1eb8b1f1589 |
| data_hash | fbce01e7860064d09669bb535ef36a03c3a110cfa15f6e9d0363d5c529092630 |
| universe_hash | a81a7b876e98494b0025afae303441c44e901728530637817e8eba0470207ecf |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 27.45% |
| annual_return | 8.03% |
| max_drawdown | -6.75% |
| sharpe | 1.075 |
| calmar | 1.190 |
| win_rate | 81.36% |
| n_trades | 3435 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601939.SH | 500 | 24.46 | 27.59 | 1568.61 |
| 002948.SZ | 1200 | 7.92 | 8.60 | 815.24 |
| 000589.SZ | 300 | 31.53 | 31.20 | -100.15 |
| 601555.SH | 900 | 11.42 | 11.71 | 263.54 |
| 600170.SH | 300 | 42.26 | 44.13 | 561.40 |
| 600467.SH | 600 | 19.45 | 20.49 | 625.41 |
| 002204.SZ | 500 | 26.30 | 27.23 | 466.77 |
| 601083.SH | 900 | 11.91 | 13.14 | 1111.70 |
| 601198.SH | 700 | 15.87 | 16.06 | 133.73 |
| 301175.SZ | 2300 | 5.45 | 5.95 | 1134.70 |
| 600958.SH | 900 | 11.56 | 11.73 | 156.00 |
| 601311.SH | 300 | 26.36 | 27.72 | 408.65 |
| 002746.SZ | 300 | 26.08 | 29.78 | 1110.74 |
| 001219.SZ | 400 | 28.65 | 29.86 | 482.96 |
| 601669.SH | 1500 | 6.45 | 6.73 | 419.24 |
| 002228.SZ | 300 | 36.71 | 37.85 | 341.46 |
| 002299.SZ | 200 | 44.54 | 45.62 | 215.33 |
| 603600.SH | 300 | 33.00 | 33.72 | 218.14 |
| 002939.SZ | 1100 | 9.13 | 9.34 | 228.55 |
| 603967.SH | 400 | 19.67 | 20.12 | 182.83 |
| 601890.SH | 300 | 25.09 | 25.41 | 96.99 |
| 002097.SZ | 300 | 35.75 | 36.01 | 76.56 |
| 601113.SH | 1700 | 6.96 | 7.22 | 431.90 |
| 000905.SZ | 400 | 21.99 | 23.69 | 678.99 |
| 300230.SZ | 300 | 32.76 | 32.92 | 50.29 |
| 000906.SZ | 200 | 31.01 | 31.81 | 160.61 |
| 601222.SH | 300 | 31.23 | 32.72 | 447.60 |
| 300140.SZ | 400 | 22.47 | 24.30 | 732.22 |
| 601002.SH | 1500 | 5.65 | 5.82 | 251.58 |
| 600469.SH | 600 | 15.63 | 15.56 | -41.01 |
| 002953.SZ | 400 | 22.26 | 23.78 | 607.81 |
| 000950.SZ | 600 | 13.12 | 13.73 | 366.69 |
| 603817.SH | 1800 | 5.80 | 6.13 | 596.16 |
| 601117.SH | 900 | 9.85 | 10.32 | 419.23 |
| 601375.SH | 2700 | 4.30 | 4.43 | 334.63 |
| 002593.SZ | 700 | 14.41 | 14.97 | 397.73 |

## 关键日志摘录

```
[ERROR] 2026-07-29 unfilled_order code=603967.SH reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=601890.SH reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=002097.SZ reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=000905.SZ reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=300230.SZ reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=000906.SZ reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=300140.SZ reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=600469.SH reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=603817.SH reason=below_min_lot
[ERROR] 2026-07-29 unfilled_order code=601117.SH reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-03T00:12:35
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-07-31, 5811 codes
- universe_coverage: 5342/5471 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
