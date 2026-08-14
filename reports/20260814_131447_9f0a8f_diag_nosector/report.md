# Backtest Report -- diag_nosector

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_131447_9f0a8f |
| run_started_at | 2026-08-14T13:14:47 |
| runtime_seconds | 152.584 |
| config_hash | e6f17ea339b6ced2e08a822d96210916e879bf030755cfa8060d661f86a8a261 |
| data_hash | 9b3443eee71a58b6368d0f9b5323b7fa14a9bf5fa87e8d5bac03fca77d9724d7 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -38.88% |
| annual_return | -20.16% |
| max_drawdown | -45.89% |
| sharpe | -0.669 |
| calmar | -0.439 |
| win_rate | 52.50% |
| n_trades | 677 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2021-02-23 unfilled_order code=603517.SH reason=below_min_lot
[ERROR] 2021-02-23 unfilled_order code=600882.SH reason=below_min_lot
[ERROR] 2021-02-24 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-25 unfilled_order code=601155.SH reason=below_min_lot
[ERROR] 2021-02-25 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2021-02-26 unfilled_order code=601155.SH reason=below_min_lot
[ERROR] 2021-02-26 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2021-03-01 unfilled_order code=601155.SH reason=below_min_lot
[ERROR] 2021-06-02 unfilled_order code=002568.SZ reason=below_min_lot
[ERROR] 2021-07-02 unfilled_order code=600687.SH reason=suspended
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
