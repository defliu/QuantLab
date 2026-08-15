# Backtest Report -- huang529_ma200

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_203813_ba185d |
| run_started_at | 2026-08-15T20:38:13 |
| runtime_seconds | 633.233 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -4.82% |
| annual_return | -0.67% |
| max_drawdown | -42.18% |
| sharpe | 0.035 |
| calmar | -0.016 |
| win_rate | 39.27% |
| n_trades | 703 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600999.SH | 2900 | 40.64 | 45.06 | 12804.81 |
| 002668.SZ | 2300 | 50.25 | 55.49 | 12037.18 |
| 000795.SZ | 2600 | 44.66 | 38.41 | -16259.13 |
| 600030.SH | 600 | 193.83 | 187.11 | -4030.51 |
| 601995.SH | 3200 | 37.02 | 36.60 | -1333.67 |

## 关键日志摘录

```
[ERROR] 2020-02-04 unfilled_order code=603167.SH reason=limit_down_at_open
[ERROR] 2020-02-04 unfilled_order code=600193.SH reason=limit_down_at_open
[ERROR] 2020-04-28 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2020-05-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2020-05-27 unfilled_order code=600611.SH reason=below_min_lot
[ERROR] 2020-11-05 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
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
