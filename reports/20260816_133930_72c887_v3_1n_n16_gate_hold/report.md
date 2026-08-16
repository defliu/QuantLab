# Backtest Report -- v3_1n_n16_gate_hold

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_133930_72c887 |
| run_started_at | 2026-08-16T13:39:30 |
| runtime_seconds | 751.541 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 54.93% |
| 年化(线性) | 7.63% |
| 年化(CAGR) | 6.27% |
| max_drawdown | -22.63% |
| sharpe | 0.483 |
| calmar | 0.337 |
| 卡玛(CAGR) | 0.277 |
| win_rate | 44.39% |
| n_trades | 772 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 1900 | 57.85 | 51.44 | -12175.87 |
| 601665.SH | 12400 | 7.47 | 7.27 | -2440.30 |
| 002458.SZ | 1100 | 91.37 | 90.16 | -1332.52 |
| 601838.SH | 4100 | 26.23 | 25.47 | -3100.17 |
| 301020.SZ | 3900 | 27.70 | 24.19 | -13714.46 |
| 601939.SH | 4300 | 25.23 | 24.38 | -3672.18 |
| 600900.SH | 900 | 105.61 | 101.04 | -4111.03 |
| 600377.SH | 2200 | 47.32 | 42.71 | -10127.06 |
| 601518.SH | 18000 | 5.47 | 4.93 | -9765.70 |
| 600999.SH | 2200 | 40.64 | 45.06 | 9714.00 |
| 600030.SH | 500 | 193.83 | 187.11 | -3358.76 |
| 601995.SH | 2600 | 37.02 | 36.60 | -1083.61 |

## 关键日志摘录

```
[ERROR] 2020-12-31 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-04 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-05 unfilled_order code=600212.SH reason=limit_up_at_open
[ERROR] 2021-01-06 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2023-04-10 unfilled_order code=600620.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2026-06-29 unfilled_order code=301020.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=301020.SZ reason=suspended
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
