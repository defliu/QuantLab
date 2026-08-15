# Backtest Report -- huang529_base

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_194655_4d19ef |
| run_started_at | 2026-08-15T19:46:55 |
| runtime_seconds | 644.032 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 0.48% |
| annual_return | 0.07% |
| max_drawdown | -33.44% |
| sharpe | 0.102 |
| calmar | 0.002 |
| win_rate | 51.27% |
| n_trades | 3909 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 2400 | 54.62 | 63.07 | 20267.48 |
| 601665.SH | 17300 | 7.54 | 7.27 | -4524.00 |
| 600919.SH | 6800 | 18.84 | 18.55 | -1936.20 |
| 601838.SH | 5000 | 26.31 | 25.47 | -4200.11 |
| 603213.SH | 8000 | 16.83 | 15.69 | -9089.28 |
| 600377.SH | 3000 | 46.20 | 42.71 | -10453.48 |
| 600483.SH | 2900 | 46.10 | 41.24 | -14093.89 |
| 600999.SH | 2800 | 41.83 | 45.06 | 9032.56 |
| 600030.SH | 600 | 193.83 | 187.11 | -4030.51 |

## 关键日志摘录

```
[ERROR] 2026-06-29 unfilled_order code=600030.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600483.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601665.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600919.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601838.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603213.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600377.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600999.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600030.SH reason=suspended
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
