# Backtest Report -- v3_1n_n12_h60_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_123422_0a5fdf |
| run_started_at | 2026-08-16T12:34:22 |
| runtime_seconds | 661.923 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 54.91% |
| 年化(线性) | 7.62% |
| 年化(CAGR) | 6.27% |
| max_drawdown | -23.19% |
| sharpe | 0.425 |
| calmar | 0.329 |
| 卡玛(CAGR) | 0.270 |
| win_rate | 44.44% |
| n_trades | 835 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 605319.SH | 3700 | 39.38 | 34.16 | -19310.92 |
| 300050.SZ | 2600 | 57.85 | 51.44 | -16661.72 |
| 601665.SH | 18200 | 7.67 | 7.27 | -7225.69 |
| 601838.SH | 5400 | 26.23 | 25.47 | -4083.15 |
| 000429.SZ | 2200 | 64.77 | 68.06 | 7227.78 |
| 601939.SH | 5000 | 25.23 | 24.38 | -4269.98 |
| 003013.SZ | 7800 | 17.68 | 17.98 | 2315.58 |
| 601518.SH | 25300 | 5.47 | 4.93 | -13726.24 |
| 601988.SH | 7300 | 15.78 | 14.88 | -6557.35 |
| 600030.SH | 600 | 193.83 | 187.11 | -4030.51 |
| 601995.SH | 3500 | 37.02 | 36.60 | -1458.70 |

## 关键日志摘录

```
[ERROR] 2021-07-29 unfilled_order code=603115.SH reason=limit_up_at_open
[ERROR] 2021-11-01 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2022-06-27 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2023-07-26 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2024-02-02 unfilled_order code=600734.SH reason=limit_down_at_open
[ERROR] 2024-02-05 unfilled_order code=600734.SH reason=limit_down_at_open
[ERROR] 2024-02-06 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2024-09-04 unfilled_order code=000538.SZ reason=below_min_lot
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
