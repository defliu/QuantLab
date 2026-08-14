# Backtest Report -- rps_momentum_v1_smoke

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_124805_6b6bae |
| run_started_at | 2026-08-14T12:48:05 |
| runtime_seconds | 209.367 |
| config_hash | 01583918ac24de123f641f15b59f8981e2cb7e350bb1bc98158998b1d1ed86bb |
| data_hash | f9c79e9cc40553728419c794732ccb352a02636e03fe53081a9888c4319d7270 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -11.85% |
| annual_return | -6.17% |
| max_drawdown | -25.32% |
| sharpe | -0.377 |
| calmar | -0.244 |
| win_rate | 53.85% |
| n_trades | 95 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2024-11-04 unfilled_order code=002371.SZ reason=below_min_lot
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
