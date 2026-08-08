# Backtest Report -- atr_lowvol_fw_leveraged

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260809_000809_3032e9 |
| run_started_at | 2026-08-09T00:08:09 |
| runtime_seconds | 520.171 |
| config_hash | e563c97d7c2fd55af9ebc0c9e5e5fa9565d71ce6e53d1bc350530862e522c82e |
| data_hash | 658f6109738361ba874b7865703c400d9ea0f89b5ae3acbe274cf364f676654d |
| universe_hash | a81a7b876e98494b0025afae303441c44e901728530637817e8eba0470207ecf |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 48.29% |
| annual_return | 14.05% |
| max_drawdown | -10.36% |
| sharpe | 1.138 |
| calmar | 1.357 |
| win_rate | 72.21% |
| n_trades | 2490 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 603167.SH | 7200 | 14.73 | 15.05 | 2330.65 |
| 002807.SZ | 2900 | 7.43 | 7.81 | 1106.42 |
| 601916.SH | 6600 | 4.13 | 4.38 | 1673.16 |
| 601939.SH | 800 | 23.76 | 27.59 | 3066.39 |
| 001227.SZ | 13500 | 2.60 | 2.65 | 753.12 |
| 603323.SH | 2600 | 9.83 | 10.18 | 907.58 |
| 600908.SH | 3000 | 7.49 | 7.78 | 851.94 |
| 002958.SZ | 6300 | 3.58 | 4.00 | 2630.43 |
| 002839.SZ | 3200 | 7.06 | 7.55 | 1568.29 |
| 601860.SH | 7900 | 3.36 | 3.41 | 427.18 |
| 002948.SZ | 1600 | 7.61 | 8.60 | 1582.35 |
| 603128.SH | 4500 | 18.11 | 18.68 | 2551.68 |
| 600572.SH | 800 | 74.25 | 77.61 | 2686.23 |
| 603213.SH | 3800 | 15.81 | 16.01 | 768.98 |
| 601377.SH | 3600 | 16.69 | 17.25 | 2026.90 |
| 601000.SH | 2000 | 33.23 | 36.55 | 6636.04 |
| 601333.SH | 17200 | 4.29 | 4.46 | 2878.23 |
| 000900.SZ | 1300 | 60.66 | 60.53 | -167.08 |
| 000828.SZ | 1800 | 43.96 | 45.71 | 3154.82 |
| 600210.SH | 1200 | 65.25 | 66.50 | 1505.12 |

## 关键日志摘录

```
[ERROR] 2026-07-03 unfilled_order code=601000.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=601333.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=000900.SZ reason=suspended
[ERROR] 2026-07-03 unfilled_order code=000828.SZ reason=suspended
[ERROR] 2026-07-03 unfilled_order code=600210.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=603111.SH reason=suspended
[ERROR] 2026-07-06 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-07-23 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-07-23 unfilled_order code=002948.SZ reason=below_min_lot
[ERROR] 2026-07-23 unfilled_order code=000900.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5342/5471 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
