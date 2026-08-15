# Backtest Report -- huang529_base

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_202727_722640 |
| run_started_at | 2026-08-15T20:27:27 |
| runtime_seconds | 634.826 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 26.59% |
| annual_return | 3.69% |
| max_drawdown | -25.05% |
| sharpe | 0.274 |
| calmar | 0.147 |
| win_rate | 38.31% |
| n_trades | 608 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 3200 | 52.72 | 63.07 | 33119.40 |
| 600919.SH | 9400 | 18.76 | 18.55 | -1949.86 |
| 000429.SZ | 2300 | 64.77 | 68.06 | 7556.32 |
| 603213.SH | 8300 | 16.89 | 15.69 | -9956.66 |
| 601398.SH | 8000 | 19.72 | 18.27 | -11641.61 |
| 601328.SH | 7500 | 19.45 | 17.72 | -13040.33 |
| 600999.SH | 3600 | 40.64 | 45.06 | 15895.63 |
| 000795.SZ | 3500 | 44.66 | 38.41 | -21887.29 |

## 关键日志摘录

```
[ERROR] 2026-06-23 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-24 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-25 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-26 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601328.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601328.SH reason=suspended
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
