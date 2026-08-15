# Backtest Report -- atr_10w_price50_liquid_pit

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_000530_d81c19 |
| run_started_at | 2026-08-16T00:05:30 |
| runtime_seconds | 707.55 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 115.27% |
| annual_return | 16.00% |
| max_drawdown | -27.43% |
| sharpe | 0.673 |
| calmar | 0.583 |
| win_rate | 68.70% |
| n_trades | 587 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 300 | 48.20 | 63.07 | 4459.40 |
| 601916.SH | 8800 | 4.14 | 4.01 | -1179.62 |
| 601939.SH | 1400 | 23.76 | 24.38 | 865.70 |
| 001227.SZ | 14800 | 2.63 | 2.46 | -2505.83 |
| 603323.SH | 3800 | 9.94 | 9.09 | -3227.25 |
| 600908.SH | 5100 | 7.54 | 6.94 | -3032.15 |

## 关键日志摘录

```
[ERROR] 2026-06-29 unfilled_order code=601916.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601939.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=001227.SZ reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600908.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603323.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600068.SH reason=suspended
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
