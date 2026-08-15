# Backtest Report -- atr_10w_price50_vt0.2

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_115409_c96ed1 |
| run_started_at | 2026-08-15T11:54:09 |
| runtime_seconds | 809.274 |
| config_hash | 7de50028f885d790f0c0f63b50a3f730058153badaf4ff1d3ce3b4a14eeb5a5d |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 160.34% |
| annual_return | 22.26% |
| max_drawdown | -22.20% |
| sharpe | 0.868 |
| calmar | 1.003 |
| win_rate | 72.77% |
| n_trades | 604 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 12800 | 4.14 | 4.01 | -1724.25 |
| 601939.SH | 2100 | 23.94 | 24.38 | 914.24 |
| 001227.SZ | 21500 | 2.62 | 2.46 | -3354.34 |
| 603323.SH | 5600 | 9.92 | 9.09 | -4617.45 |
| 600908.SH | 7500 | 7.52 | 6.94 | -4295.47 |

## 关键日志摘录

```
[ERROR] 2026-06-29 unfilled_order code=603323.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601916.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601939.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=001227.SZ reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600908.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603323.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601916.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601939.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=001227.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600908.SH reason=suspended
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
