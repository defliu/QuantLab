# Backtest Report -- v2_n12_h20_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_215608_69d637 |
| run_started_at | 2026-08-15T21:56:08 |
| runtime_seconds | 725.586 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -24.52% |
| annual_return | -3.40% |
| max_drawdown | -32.23% |
| sharpe | -0.131 |
| calmar | -0.106 |
| win_rate | 45.91% |
| n_trades | 2041 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 301618.SZ | 900 | 78.89 | 93.73 | 13364.30 |
| 600900.SH | 400 | 105.61 | 101.04 | -1827.12 |
| 000429.SZ | 700 | 68.87 | 68.06 | -572.45 |
| 600377.SH | 1900 | 47.32 | 42.71 | -8746.10 |
| 600483.SH | 1300 | 46.38 | 41.24 | -6682.01 |
| 601518.SH | 18100 | 5.47 | 4.93 | -9819.96 |
| 601988.SH | 600 | 15.78 | 14.88 | -538.96 |
| 601398.SH | 4900 | 19.72 | 18.27 | -7130.48 |
| 600999.SH | 1700 | 40.64 | 45.06 | 7506.27 |
| 000795.SZ | 2100 | 44.66 | 38.41 | -13132.38 |
| 600030.SH | 100 | 193.83 | 187.11 | -671.75 |

## 关键日志摘录

```
[ERROR] 2026-01-07 unfilled_order code=000423.SZ reason=below_min_lot
[ERROR] 2026-02-06 unfilled_order code=000423.SZ reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=603529.SH reason=limit_down_at_open
[ERROR] 2026-05-12 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-06-01 unfilled_order code=600377.SH reason=below_min_lot
[ERROR] 2026-06-15 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=301618.SZ reason=suspended
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=301618.SZ reason=suspended
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
