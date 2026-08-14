# Backtest Report -- v3_balanced_2020

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_133943_d2a5d2 |
| run_started_at | 2026-08-14T13:39:43 |
| runtime_seconds | 147.457 |
| config_hash | 53ae2c1cef04631d3142d4e9d25ad616152019fd2adf7f368622935c07a1f4a9 |
| data_hash | 9b3443eee71a58b6368d0f9b5323b7fa14a9bf5fa87e8d5bac03fca77d9724d7 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 56.47% |
| annual_return | 29.28% |
| max_drawdown | -41.65% |
| sharpe | 0.839 |
| calmar | 0.703 |
| win_rate | 60.78% |
| n_trades | 165 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2020-06-02 unfilled_order code=002626.SZ reason=below_min_lot
[ERROR] 2020-07-02 unfilled_order code=002626.SZ reason=below_min_lot
[ERROR] 2020-11-03 unfilled_order code=000568.SZ reason=below_min_lot
[ERROR] 2021-01-05 unfilled_order code=000768.SZ reason=below_min_lot
[ERROR] 2021-01-05 unfilled_order code=002801.SZ reason=below_min_lot
[ERROR] 2021-06-02 unfilled_order code=002176.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 4648/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
