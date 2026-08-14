# Backtest Report -- diag_baseline

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_130548_428be0 |
| run_started_at | 2026-08-14T13:05:48 |
| runtime_seconds | 132.365 |
| config_hash | 4fd1025124401f01c376bcd9b98661c7cd79ad664312c05fdf9718bdbe759747 |
| data_hash | 9b3443eee71a58b6368d0f9b5323b7fa14a9bf5fa87e8d5bac03fca77d9724d7 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -14.66% |
| annual_return | -7.60% |
| max_drawdown | -21.97% |
| sharpe | -0.447 |
| calmar | -0.346 |
| win_rate | 50.00% |
| n_trades | 31 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2020-11-03 unfilled_order code=000568.SZ reason=below_min_lot
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
