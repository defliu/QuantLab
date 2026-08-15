# Backtest Report -- atr_10w_price50_vt0.15

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_112755_3aa944 |
| run_started_at | 2026-08-15T11:27:55 |
| runtime_seconds | 781.865 |
| config_hash | 22fdd4d7e8f9dd8e81512a62e33bc17fc9bd9aaeef102cd7c6c3edbb812de952 |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 102.79% |
| annual_return | 14.27% |
| max_drawdown | -20.86% |
| sharpe | 0.797 |
| calmar | 0.684 |
| win_rate | 71.82% |
| n_trades | 543 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 8900 | 4.16 | 4.01 | -1317.42 |
| 601939.SH | 1500 | 24.17 | 24.38 | 311.15 |
| 001227.SZ | 15000 | 2.60 | 2.46 | -2130.09 |
| 603323.SH | 3900 | 9.82 | 9.09 | -2830.82 |
| 600908.SH | 5200 | 7.47 | 6.94 | -2741.22 |

## 关键日志摘录

```
[ERROR] 2025-01-03 unfilled_order code=000513.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=002746.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=600866.SH reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=000915.SZ reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=000915.SZ reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=603323.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=603128.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=600572.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=600908.SH reason=below_min_lot
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
