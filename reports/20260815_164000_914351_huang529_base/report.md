# Backtest Report -- huang529_base

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_164000_914351 |
| run_started_at | 2026-08-15T16:40:00 |
| runtime_seconds | 691.849 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 56.70% |
| annual_return | 7.87% |
| max_drawdown | -23.74% |
| sharpe | 0.405 |
| calmar | 0.332 |
| win_rate | 51.52% |
| n_trades | 4671 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 2800 | 54.80 | 63.07 | 23141.97 |
| 601665.SH | 26800 | 7.55 | 7.27 | -7272.47 |
| 600919.SH | 10600 | 18.84 | 18.55 | -3075.10 |
| 601838.SH | 7700 | 26.35 | 25.47 | -6733.03 |
| 603213.SH | 12400 | 16.82 | 15.69 | -14057.78 |
| 301618.SZ | 2200 | 78.62 | 93.73 | 33260.11 |
| 600377.SH | 4600 | 46.16 | 42.71 | -15825.95 |
| 600999.SH | 4400 | 41.83 | 45.06 | 14194.02 |
| 000795.SZ | 4700 | 44.48 | 38.41 | -28521.83 |

## 关键日志摘录

```
[ERROR] 2026-06-29 unfilled_order code=600999.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601665.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600919.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601838.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603213.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=301618.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600377.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600999.SH reason=suspended
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
