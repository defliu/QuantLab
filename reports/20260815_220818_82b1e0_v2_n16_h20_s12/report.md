# Backtest Report -- v2_n16_h20_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_220818_82b1e0 |
| run_started_at | 2026-08-15T22:08:18 |
| runtime_seconds | 794.953 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -27.37% |
| annual_return | -3.80% |
| max_drawdown | -35.91% |
| sharpe | -0.170 |
| calmar | -0.106 |
| win_rate | 44.38% |
| n_trades | 2653 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 301618.SZ | 600 | 78.89 | 93.73 | 8909.53 |
| 600377.SH | 900 | 46.59 | 42.71 | -3487.72 |
| 600900.SH | 400 | 105.61 | 101.04 | -1827.12 |
| 000429.SZ | 700 | 68.87 | 68.06 | -572.45 |
| 600012.SH | 1600 | 52.59 | 47.82 | -7640.32 |
| 003013.SZ | 1600 | 17.68 | 17.98 | 474.99 |
| 600483.SH | 600 | 45.05 | 41.24 | -2285.51 |
| 603683.SH | 700 | 54.95 | 53.51 | -1005.00 |
| 601518.SH | 8500 | 5.47 | 4.93 | -4611.58 |
| 601988.SH | 1000 | 15.78 | 14.88 | -898.27 |
| 601398.SH | 4000 | 19.72 | 18.27 | -5820.80 |
| 601939.SH | 3000 | 26.20 | 24.38 | -5481.74 |
| 600999.SH | 1000 | 40.64 | 45.06 | 4415.45 |
| 000795.SZ | 900 | 44.66 | 38.41 | -5628.16 |
| 601995.SH | 1300 | 37.02 | 36.60 | -541.80 |

## 关键日志摘录

```
[ERROR] 2026-04-23 unfilled_order code=603529.SH reason=limit_down_at_open
[ERROR] 2026-05-12 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-05-21 unfilled_order code=002032.SZ reason=below_min_lot
[ERROR] 2026-05-22 unfilled_order code=002032.SZ reason=below_min_lot
[ERROR] 2026-06-15 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=301618.SZ reason=suspended
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=301618.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600377.SH reason=suspended
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
