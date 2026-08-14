# Backtest Report -- rps_momentum_v1_smoke

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_122727_6a00b4 |
| run_started_at | 2026-08-14T12:27:27 |
| runtime_seconds | 211.452 |
| config_hash | 7f003cd0f547e04fa1834948388051754f3ba8b729589900fbc638f406ba4152 |
| data_hash | f9c79e9cc40553728419c794732ccb352a02636e03fe53081a9888c4319d7270 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -46.78% |
| annual_return | -24.36% |
| max_drawdown | -48.30% |
| sharpe | -1.389 |
| calmar | -0.504 |
| win_rate | 59.40% |
| n_trades | 1216 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 301088.SZ | 7200 | 15.66 | 14.54 | -8054.19 |
| 002801.SZ | 1000 | 109.92 | 107.11 | -2812.92 |
| 301193.SZ | 4000 | 27.76 | 27.19 | -2260.22 |
| 003008.SZ | 4100 | 27.38 | 25.60 | -7283.10 |
| 301109.SZ | 3700 | 29.40 | 28.29 | -4117.94 |

## 关键日志摘录

```
[ERROR] 2024-12-27 unfilled_order code=301088.SZ reason=below_min_lot
[ERROR] 2024-12-27 unfilled_order code=003008.SZ reason=below_min_lot
[ERROR] 2024-12-27 unfilled_order code=301109.SZ reason=below_min_lot
[ERROR] 2024-12-30 unfilled_order code=002801.SZ reason=below_min_lot
[ERROR] 2024-12-30 unfilled_order code=003008.SZ reason=below_min_lot
[ERROR] 2024-12-30 unfilled_order code=301109.SZ reason=below_min_lot
[ERROR] 2024-12-31 unfilled_order code=002801.SZ reason=below_min_lot
[ERROR] 2024-12-31 unfilled_order code=301193.SZ reason=below_min_lot
[ERROR] 2024-12-31 unfilled_order code=003008.SZ reason=below_min_lot
[ERROR] 2024-12-31 unfilled_order code=301109.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5229/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
