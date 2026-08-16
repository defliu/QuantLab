# Backtest Report -- attrib_20d_s08

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_085901_cdfc66 |
| run_started_at | 2026-08-16T08:59:01 |
| runtime_seconds | 662.392 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -16.82% |
| annual_return | -2.33% |
| max_drawdown | -32.67% |
| sharpe | -0.062 |
| calmar | -0.071 |
| win_rate | 45.69% |
| n_trades | 1477 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600377.SH | 2400 | 46.59 | 42.71 | -9300.58 |
| 600900.SH | 1000 | 105.61 | 101.04 | -4567.81 |
| 000429.SZ | 1500 | 68.87 | 68.06 | -1226.69 |
| 601988.SH | 6700 | 15.78 | 14.88 | -6018.39 |
| 601398.SH | 5500 | 19.72 | 18.27 | -8003.61 |
| 600999.SH | 2600 | 40.64 | 45.06 | 11480.18 |
| 601995.SH | 2700 | 37.02 | 36.60 | -1125.28 |

## 关键日志摘录

```
[ERROR] 2024-05-08 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2024-09-04 unfilled_order code=000538.SZ reason=below_min_lot
[ERROR] 2024-12-05 unfilled_order code=603511.SH reason=limit_up_at_open
[ERROR] 2025-09-09 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2025-11-14 unfilled_order code=002242.SZ reason=limit_up_at_open
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-05-12 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-06-15 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=600377.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600377.SH reason=suspended
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
