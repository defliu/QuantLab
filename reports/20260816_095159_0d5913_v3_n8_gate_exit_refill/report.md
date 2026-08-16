# Backtest Report -- v3_n8_gate_exit_refill

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_095159_0d5913 |
| run_started_at | 2026-08-16T09:51:59 |
| runtime_seconds | 603.055 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -6.46% |
| annual_return | -0.90% |
| max_drawdown | -44.29% |
| sharpe | 0.016 |
| calmar | -0.020 |
| win_rate | 41.69% |
| n_trades | 702 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600999.SH | 2900 | 40.64 | 45.06 | 12804.81 |
| 002668.SZ | 2400 | 50.25 | 55.49 | 12560.53 |
| 601328.SH | 6300 | 19.07 | 17.72 | -8550.69 |
| 601398.SH | 6200 | 19.26 | 18.27 | -6167.75 |
| 600919.SH | 6100 | 19.75 | 18.55 | -7293.27 |
| 601838.SH | 4200 | 28.26 | 25.47 | -11705.34 |
| 601988.SH | 7600 | 15.78 | 14.88 | -6826.83 |
| 000795.SZ | 2600 | 44.66 | 38.41 | -16259.13 |

## 关键日志摘录

```
[ERROR] 2023-04-07 unfilled_order code=600620.SH reason=below_min_lot
[ERROR] 2025-07-04 unfilled_order code=002788.SZ reason=below_min_lot
[ERROR] 2025-07-04 unfilled_order code=600219.SH reason=below_min_lot
[ERROR] 2025-07-04 unfilled_order code=000709.SZ reason=below_min_lot
[ERROR] 2025-09-30 unfilled_order code=600031.SH reason=below_min_lot
[ERROR] 2025-10-09 unfilled_order code=600031.SH reason=below_min_lot
[ERROR] 2026-06-16 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-18 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=000795.SZ reason=suspended
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5443/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
