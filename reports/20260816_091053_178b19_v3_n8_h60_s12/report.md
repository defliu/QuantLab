# Backtest Report -- v3_n8_h60_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_091053_178b19 |
| run_started_at | 2026-08-16T09:10:53 |
| runtime_seconds | 616.499 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 32.04% |
| annual_return | 4.45% |
| max_drawdown | -32.80% |
| sharpe | 0.297 |
| calmar | 0.136 |
| win_rate | 45.10% |
| n_trades | 565 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 605319.SH | 4800 | 39.38 | 34.16 | -25052.01 |
| 601665.SH | 24900 | 7.67 | 7.27 | -9885.70 |
| 601838.SH | 7000 | 26.23 | 25.47 | -5292.97 |
| 000429.SZ | 2600 | 64.77 | 68.06 | 8541.92 |
| 601518.SH | 27700 | 5.47 | 4.93 | -15028.33 |
| 600030.SH | 800 | 193.83 | 187.11 | -5374.02 |
| 601995.SH | 4500 | 37.02 | 36.60 | -1875.47 |

## 关键日志摘录

```
[ERROR] 2020-11-05 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2021-05-10 unfilled_order code=002716.SZ reason=limit_up_at_open
[ERROR] 2021-07-29 unfilled_order code=603115.SH reason=limit_up_at_open
[ERROR] 2021-11-01 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2024-02-02 unfilled_order code=600734.SH reason=limit_down_at_open
[ERROR] 2024-02-05 unfilled_order code=600734.SH reason=limit_down_at_open
[ERROR] 2024-02-06 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=605319.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=605319.SH reason=suspended
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
