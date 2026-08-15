# Backtest Report -- v2_n8_h20_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_214534_315a3a |
| run_started_at | 2026-08-15T21:45:34 |
| runtime_seconds | 630.772 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -16.78% |
| annual_return | -2.33% |
| max_drawdown | -30.34% |
| sharpe | -0.060 |
| calmar | -0.077 |
| win_rate | 47.84% |
| n_trades | 1386 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600900.SH | 1100 | 105.61 | 101.04 | -5024.59 |
| 600377.SH | 2200 | 47.32 | 42.71 | -10127.06 |
| 601518.SH | 18300 | 5.47 | 4.93 | -9928.46 |
| 601398.SH | 5600 | 19.72 | 18.27 | -8149.13 |
| 601988.SH | 6900 | 16.09 | 14.88 | -8340.26 |
| 601995.SH | 2800 | 37.02 | 36.60 | -1166.96 |

## 关键日志摘录

```
[ERROR] 2024-09-23 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2024-09-24 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2024-12-05 unfilled_order code=603511.SH reason=limit_up_at_open
[ERROR] 2025-06-13 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2025-06-20 unfilled_order code=002125.SZ reason=limit_up_at_open
[ERROR] 2025-09-09 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2025-09-11 unfilled_order code=600795.SH reason=below_min_lot
[ERROR] 2025-11-14 unfilled_order code=002242.SZ reason=limit_up_at_open
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-05-12 unfilled_order code=600887.SH reason=below_min_lot
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
