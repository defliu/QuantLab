# Backtest Report -- v2_n8_gate_hold

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_232739_7f0a40 |
| run_started_at | 2026-08-15T23:27:39 |
| runtime_seconds | 786.635 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -21.52% |
| annual_return | -2.99% |
| max_drawdown | -37.90% |
| sharpe | -0.156 |
| calmar | -0.079 |
| win_rate | 47.02% |
| n_trades | 933 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 301618.SZ | 1300 | 78.89 | 93.73 | 19303.98 |
| 600377.SH | 2100 | 46.59 | 42.71 | -8138.01 |
| 600900.SH | 900 | 105.61 | 101.04 | -4111.03 |
| 600012.SH | 1900 | 52.59 | 47.82 | -9072.88 |
| 600483.SH | 2100 | 46.38 | 41.24 | -10794.02 |
| 600999.SH | 2300 | 40.64 | 45.06 | 10155.54 |
| 601995.SH | 2600 | 37.02 | 36.60 | -1083.61 |

## 关键日志摘录

```
[ERROR] 2025-06-13 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2025-06-20 unfilled_order code=002125.SZ reason=limit_up_at_open
[ERROR] 2025-09-09 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2025-09-11 unfilled_order code=600795.SH reason=below_min_lot
[ERROR] 2025-11-14 unfilled_order code=002242.SZ reason=limit_up_at_open
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-03-09 unfilled_order code=600674.SH reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=301618.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=301618.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600377.SH reason=suspended
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
