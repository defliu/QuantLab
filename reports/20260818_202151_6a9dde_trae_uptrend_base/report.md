# Backtest Report -- trae_uptrend_base

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260818_202151_6a9dde |
| run_started_at | 2026-08-18T20:21:51 |
| runtime_seconds | 695.563 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 10.75% |
| 年化(线性) | 1.49% |
| 年化(CAGR) | 1.43% |
| max_drawdown | -36.85% |
| sharpe | 0.172 |
| calmar | 0.041 |
| 卡玛(CAGR) | 0.039 |
| win_rate | 38.60% |
| n_trades | 536 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 001308.SZ | 3600 | 38.22 | 39.81 | 5738.03 |
| 603075.SH | 5200 | 26.72 | 24.41 | -12018.43 |
| 300041.SZ | 1400 | 99.32 | 128.49 | 40833.99 |
| 300307.SZ | 8400 | 16.56 | 16.74 | 1543.66 |
| 600011.SH | 3200 | 42.80 | 40.15 | -8465.72 |
| 300826.SZ | 3600 | 37.27 | 35.20 | -7435.22 |
| 600999.SH | 2700 | 45.62 | 46.42 | 2162.90 |
| 000783.SZ | 2000 | 61.90 | 64.60 | 5411.34 |

## 关键日志摘录

```
[ERROR] 2020-06-12 unfilled_order code=600182.SH reason=suspended
[ERROR] 2024-03-05 unfilled_order code=600938.SH reason=below_min_lot
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
