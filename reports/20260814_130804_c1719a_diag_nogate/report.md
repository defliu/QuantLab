# Backtest Report -- diag_nogate

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_130804_c1719a |
| run_started_at | 2026-08-14T13:08:04 |
| runtime_seconds | 130.214 |
| config_hash | 1e717985b4f314cde4e5e24017e383b3f0693aeb1b50643dbddb5190611d65aa |
| data_hash | 9b3443eee71a58b6368d0f9b5323b7fa14a9bf5fa87e8d5bac03fca77d9724d7 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -30.94% |
| annual_return | -16.04% |
| max_drawdown | -36.26% |
| sharpe | -0.669 |
| calmar | -0.442 |
| win_rate | 48.57% |
| n_trades | 77 |

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
