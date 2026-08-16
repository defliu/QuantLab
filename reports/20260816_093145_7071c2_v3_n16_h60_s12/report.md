# Backtest Report -- v3_n16_h60_s12

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_093145_7071c2 |
| run_started_at | 2026-08-16T09:31:45 |
| runtime_seconds | 611.654 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 82.26% |
| annual_return | 11.42% |
| max_drawdown | -32.26% |
| sharpe | 0.530 |
| calmar | 0.354 |
| win_rate | 45.08% |
| n_trades | 1104 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601000.SH | 2800 | 34.33 | 30.82 | -9808.95 |
| 300050.SZ | 2500 | 57.85 | 51.44 | -16020.88 |
| 601665.SH | 9800 | 7.67 | 7.27 | -3890.76 |
| 601838.SH | 5300 | 26.23 | 25.47 | -4007.53 |
| 000429.SZ | 3700 | 64.77 | 68.06 | 12155.81 |
| 601939.SH | 9100 | 25.23 | 24.38 | -7771.36 |
| 600900.SH | 800 | 105.61 | 101.04 | -3654.25 |
| 601518.SH | 44300 | 5.47 | 4.93 | -24034.48 |
| 601988.SH | 200 | 15.78 | 14.88 | -179.65 |
| 601398.SH | 800 | 19.72 | 18.27 | -1164.16 |
| 600999.SH | 4500 | 41.83 | 45.06 | 14516.61 |
| 000795.SZ | 1900 | 44.66 | 38.41 | -11881.67 |
| 600030.SH | 800 | 193.83 | 187.11 | -5374.02 |
| 601995.SH | 5000 | 37.02 | 36.60 | -2083.86 |

## 关键日志摘录

```
[ERROR] 2025-02-11 unfilled_order code=601728.SH reason=below_min_lot
[ERROR] 2025-03-03 unfilled_order code=600031.SH reason=below_min_lot
[ERROR] 2025-03-04 unfilled_order code=000895.SZ reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2025-06-05 unfilled_order code=000598.SZ reason=below_min_lot
[ERROR] 2025-12-03 unfilled_order code=600750.SH reason=below_min_lot
[ERROR] 2025-12-04 unfilled_order code=000333.SZ reason=below_min_lot
[ERROR] 2026-02-06 unfilled_order code=600519.SH reason=below_min_lot
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
