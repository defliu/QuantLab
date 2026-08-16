# Backtest Report -- v3_1n_n16_h60_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_131617_77faf4 |
| run_started_at | 2026-08-16T13:16:17 |
| runtime_seconds | 700.121 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 56.70% |
| 年化(线性) | 7.87% |
| 年化(CAGR) | 6.43% |
| max_drawdown | -22.94% |
| sharpe | 0.458 |
| calmar | 0.343 |
| 卡玛(CAGR) | 0.280 |
| win_rate | 44.44% |
| n_trades | 1067 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 1700 | 52.72 | 63.07 | 17594.68 |
| 300050.SZ | 1900 | 57.85 | 51.44 | -12175.87 |
| 601665.SH | 14700 | 7.67 | 7.27 | -5836.14 |
| 601838.SH | 4100 | 26.23 | 25.47 | -3100.17 |
| 000429.SZ | 1600 | 64.77 | 68.06 | 5256.57 |
| 601939.SH | 4200 | 25.23 | 24.38 | -3586.78 |
| 600900.SH | 1000 | 105.61 | 101.04 | -4567.81 |
| 601518.SH | 18400 | 5.47 | 4.93 | -9982.72 |
| 000333.SZ | 100 | 477.23 | 443.07 | -3415.75 |
| 600999.SH | 2500 | 40.64 | 45.06 | 11038.63 |
| 000795.SZ | 2200 | 44.66 | 38.41 | -13757.73 |
| 600030.SH | 500 | 193.83 | 187.11 | -3358.76 |
| 601995.SH | 2600 | 37.02 | 36.60 | -1083.61 |

## 关键日志摘录

```
[ERROR] 2026-06-18 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-22 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-23 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-24 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-25 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-26 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600068.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600068.SH reason=suspended
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
