# Backtest Report -- v3_n16_gate_hold

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_100311_e80823 |
| run_started_at | 2026-08-16T10:03:11 |
| runtime_seconds | 676.816 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 66.26% |
| annual_return | 9.20% |
| max_drawdown | -24.84% |
| sharpe | 0.514 |
| calmar | 0.370 |
| win_rate | 43.67% |
| n_trades | 762 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 4300 | 57.85 | 51.44 | -27555.92 |
| 601665.SH | 300 | 7.47 | 7.27 | -59.04 |
| 002458.SZ | 1500 | 91.37 | 90.16 | -1817.07 |
| 601838.SH | 2200 | 25.73 | 25.47 | -569.97 |
| 601939.SH | 8600 | 25.23 | 24.38 | -7344.37 |
| 600900.SH | 1200 | 105.61 | 101.04 | -5481.37 |
| 600483.SH | 2800 | 46.38 | 41.24 | -14392.02 |
| 601518.SH | 21700 | 5.47 | 4.93 | -11773.10 |
| 600999.SH | 800 | 40.64 | 45.06 | 3532.36 |
| 002668.SZ | 600 | 50.25 | 55.49 | 3140.13 |
| 600030.SH | 800 | 193.83 | 187.11 | -5374.02 |
| 601995.SH | 5600 | 37.02 | 36.60 | -2333.92 |

## 关键日志摘录

```
[ERROR] 2025-03-04 unfilled_order code=000895.SZ reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2025-06-04 unfilled_order code=000598.SZ reason=below_min_lot
[ERROR] 2025-06-05 unfilled_order code=000598.SZ reason=below_min_lot
[ERROR] 2025-12-02 unfilled_order code=600750.SH reason=below_min_lot
[ERROR] 2025-12-03 unfilled_order code=600750.SH reason=below_min_lot
[ERROR] 2025-12-04 unfilled_order code=000333.SZ reason=below_min_lot
[ERROR] 2026-01-07 unfilled_order code=000423.SZ reason=below_min_lot
[ERROR] 2026-04-15 unfilled_order code=002415.SZ reason=below_min_lot
[ERROR] 2026-04-16 unfilled_order code=600572.SH reason=below_min_lot
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
