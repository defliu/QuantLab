# Backtest Report -- v3_n8_gate_hold

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_094200_6dfa6d |
| run_started_at | 2026-08-16T09:42:00 |
| runtime_seconds | 596.267 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 69.73% |
| annual_return | 9.68% |
| max_drawdown | -21.76% |
| sharpe | 0.517 |
| calmar | 0.445 |
| win_rate | 45.18% |
| n_trades | 387 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 4300 | 57.85 | 51.44 | -27555.92 |
| 601665.SH | 30200 | 7.67 | 7.27 | -11989.88 |
| 601838.SH | 9000 | 26.23 | 25.47 | -6805.24 |
| 601939.SH | 8900 | 25.23 | 24.38 | -7600.56 |
| 601518.SH | 35400 | 5.47 | 4.93 | -19205.88 |
| 600030.SH | 1100 | 193.83 | 187.11 | -7389.28 |
| 601995.SH | 5700 | 37.02 | 36.60 | -2375.60 |

## 关键日志摘录

```
[ERROR] 2020-02-04 unfilled_order code=600193.SH reason=limit_down_at_open
[ERROR] 2020-04-23 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2020-06-02 unfilled_order code=600865.SH reason=limit_up_at_open
[ERROR] 2020-12-31 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-04 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-05 unfilled_order code=600212.SH reason=limit_up_at_open
[ERROR] 2021-01-06 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
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
