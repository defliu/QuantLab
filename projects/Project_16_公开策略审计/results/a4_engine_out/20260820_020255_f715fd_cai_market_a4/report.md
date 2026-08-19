# Backtest Report -- cai_market_a4

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260820_020255_f715fd |
| run_started_at | 2026-08-20T02:02:55 |
| runtime_seconds | 1047.561 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 903.13% |
| 年化(线性) | 125.39% |
| 年化(CAGR) | 37.73% |
| max_drawdown | -32.65% |
| sharpe | 1.369 |
| calmar | 3.841 |
| 卡玛(CAGR) | 1.156 |
| win_rate | 58.61% |
| n_trades | 3741 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 002107.SZ | 20100 | 60.37 | 49.45 | -219342.66 |
| 002404.SZ | 28000 | 41.65 | 36.12 | -154818.52 |
| 000419.SZ | 13300 | 98.00 | 75.53 | -298933.57 |
| 603689.SH | 81900 | 14.38 | 12.25 | -174533.24 |
| 002763.SZ | 48600 | 25.13 | 20.23 | -238012.96 |
| 002998.SZ | 136000 | 9.69 | 7.55 | -290599.09 |
| 002521.SZ | 37400 | 27.00 | 27.85 | 31887.28 |
| 000850.SZ | 30300 | 35.76 | 32.82 | -88960.68 |
| 002391.SZ | 30900 | 34.36 | 32.26 | -64783.73 |
| 600356.SH | 58300 | 17.40 | 17.06 | -20056.66 |

## 关键日志摘录

```
[ERROR] 2026-05-26 unfilled_order code=000419.SZ reason=below_min_lot
[ERROR] 2026-05-26 unfilled_order code=603689.SH reason=below_min_lot
[ERROR] 2026-06-09 unfilled_order code=002763.SZ reason=below_min_lot
[ERROR] 2026-06-09 unfilled_order code=000419.SZ reason=below_min_lot
[ERROR] 2026-06-16 unfilled_order code=002763.SZ reason=below_min_lot
[ERROR] 2026-06-16 unfilled_order code=002107.SZ reason=below_min_lot
[ERROR] 2026-06-23 unfilled_order code=002107.SZ reason=below_min_lot
[ERROR] 2026-06-23 unfilled_order code=000419.SZ reason=below_min_lot
[ERROR] 2026-06-30 unfilled_order code=000419.SZ reason=below_min_lot
[ERROR] 2026-06-30 unfilled_order code=002391.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-16T15:21:30
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-14, 5820 codes
- universe_coverage: 5433/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
