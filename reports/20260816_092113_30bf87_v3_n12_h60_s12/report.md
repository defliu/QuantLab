# Backtest Report -- v3_n12_h60_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_092113_30bf87 |
| run_started_at | 2026-08-16T09:21:13 |
| runtime_seconds | 628.774 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 39.82% |
| annual_return | 5.53% |
| max_drawdown | -31.00% |
| sharpe | 0.340 |
| calmar | 0.178 |
| win_rate | 44.68% |
| n_trades | 835 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 1600 | 57.85 | 51.44 | -10253.36 |
| 601665.SH | 17100 | 7.67 | 7.27 | -6788.97 |
| 601838.SH | 6200 | 26.23 | 25.47 | -4688.06 |
| 000429.SZ | 2500 | 64.77 | 68.06 | 8213.39 |
| 601939.SH | 7400 | 25.23 | 24.38 | -6319.57 |
| 601518.SH | 34900 | 5.47 | 4.93 | -18934.61 |
| 601988.SH | 600 | 15.78 | 14.88 | -538.96 |
| 600999.SH | 1200 | 40.64 | 45.06 | 5298.54 |
| 000795.SZ | 4000 | 44.66 | 38.41 | -25014.05 |
| 600030.SH | 800 | 193.83 | 187.11 | -5374.02 |
| 601995.SH | 3900 | 37.02 | 36.60 | -1625.41 |

## 关键日志摘录

```
[ERROR] 2024-02-02 unfilled_order code=600734.SH reason=limit_down_at_open
[ERROR] 2024-02-05 unfilled_order code=600734.SH reason=limit_down_at_open
[ERROR] 2024-02-06 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2024-09-04 unfilled_order code=000538.SZ reason=below_min_lot
[ERROR] 2024-09-19 unfilled_order code=600406.SH reason=below_min_lot
[ERROR] 2024-12-25 unfilled_order code=601398.SH reason=below_min_lot
[ERROR] 2025-03-03 unfilled_order code=600031.SH reason=below_min_lot
[ERROR] 2026-06-15 unfilled_order code=000001.SZ reason=below_min_lot
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
