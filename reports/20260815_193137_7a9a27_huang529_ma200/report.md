# Backtest Report -- huang529_ma200

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_193137_7a9a27 |
| run_started_at | 2026-08-15T19:31:37 |
| runtime_seconds | 664.808 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -15.08% |
| annual_return | -2.09% |
| max_drawdown | -54.40% |
| sharpe | -0.045 |
| calmar | -0.038 |
| win_rate | 38.03% |
| n_trades | 1235 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600999.SH | 3800 | 40.64 | 45.06 | 16778.72 |
| 002668.SZ | 3400 | 50.25 | 55.49 | 17794.09 |
| 000795.SZ | 4200 | 44.66 | 38.41 | -26264.75 |
| 600030.SH | 900 | 193.83 | 187.11 | -6045.77 |
| 601995.SH | 4600 | 37.02 | 36.60 | -1917.15 |

## 关键日志摘录

```
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600999.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=002668.SZ reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600030.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601995.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600999.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=002668.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600030.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601995.SH reason=suspended
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
