# Backtest Report -- atr_lowvol_fw_leveraged

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260809_003205_4498ce |
| run_started_at | 2026-08-09T00:32:05 |
| runtime_seconds | 612.932 |
| config_hash | 217fec2072c46457727743de01be00607aaff2ed2af9778e4de5fcae866bdfbf |
| data_hash | 658f6109738361ba874b7865703c400d9ea0f89b5ae3acbe274cf364f676654d |
| universe_hash | a81a7b876e98494b0025afae303441c44e901728530637817e8eba0470207ecf |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 71.84% |
| annual_return | 20.90% |
| max_drawdown | -15.81% |
| sharpe | 1.076 |
| calmar | 1.322 |
| win_rate | 70.10% |
| n_trades | 2883 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 603167.SH | 12800 | 14.73 | 15.05 | 4047.95 |
| 002807.SZ | 4600 | 7.47 | 7.81 | 1529.76 |
| 601916.SH | 10600 | 4.14 | 4.38 | 2562.72 |
| 601939.SH | 1300 | 23.57 | 27.59 | 5226.91 |
| 001227.SZ | 21500 | 2.62 | 2.65 | 759.16 |
| 603323.SH | 4100 | 9.87 | 10.18 | 1263.08 |
| 600908.SH | 4800 | 7.54 | 7.78 | 1119.48 |
| 002958.SZ | 10000 | 3.61 | 4.00 | 3929.91 |
| 002839.SZ | 5100 | 7.11 | 7.55 | 2261.13 |
| 601860.SH | 12600 | 3.38 | 3.41 | 438.37 |
| 002948.SZ | 2600 | 7.63 | 8.60 | 2504.37 |
| 603128.SH | 8000 | 18.13 | 18.68 | 4413.74 |
| 600572.SH | 1400 | 74.22 | 77.61 | 4749.76 |
| 603213.SH | 6800 | 15.85 | 16.01 | 1120.49 |
| 601377.SH | 6400 | 16.74 | 17.25 | 3284.89 |
| 601333.SH | 30200 | 4.29 | 4.46 | 4986.59 |
| 000828.SZ | 3200 | 44.00 | 45.71 | 5460.42 |
| 600210.SH | 2200 | 65.30 | 66.50 | 2652.50 |

## 关键日志摘录

```
[ERROR] 2026-07-06 unfilled_order code=603323.SH reason=below_min_lot
[ERROR] 2026-07-06 unfilled_order code=600908.SH reason=below_min_lot
[ERROR] 2026-07-06 unfilled_order code=002839.SZ reason=below_min_lot
[ERROR] 2026-07-06 unfilled_order code=002807.SZ reason=below_min_lot
[ERROR] 2026-07-06 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-07-06 unfilled_order code=001227.SZ reason=below_min_lot
[ERROR] 2026-07-06 unfilled_order code=002948.SZ reason=below_min_lot
[ERROR] 2026-07-23 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-07-23 unfilled_order code=600908.SH reason=below_min_lot
[ERROR] 2026-07-23 unfilled_order code=603128.SH reason=below_min_lot
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
