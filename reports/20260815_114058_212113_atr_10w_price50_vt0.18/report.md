# Backtest Report -- atr_10w_price50_vt0.18

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_114058_212113 |
| run_started_at | 2026-08-15T11:40:58 |
| runtime_seconds | 789.807 |
| config_hash | d8a72fe0496f4e28d363dacc9d5a38ae4f78f0e22893164d6d900000c6c69623 |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 146.56% |
| annual_return | 20.35% |
| max_drawdown | -21.05% |
| sharpe | 0.880 |
| calmar | 0.967 |
| win_rate | 72.93% |
| n_trades | 580 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 12100 | 4.15 | 4.01 | -1760.61 |
| 601939.SH | 2000 | 24.02 | 24.38 | 707.67 |
| 001227.SZ | 20300 | 2.61 | 2.46 | -3093.76 |
| 603323.SH | 5300 | 9.86 | 9.09 | -4072.55 |
| 600908.SH | 7000 | 7.51 | 6.94 | -3961.68 |

## 关键日志摘录

```
[ERROR] 2024-11-01 unfilled_order code=600919.SH reason=below_min_lot
[ERROR] 2025-01-03 unfilled_order code=000513.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=002746.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=301559.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=600866.SH reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=000915.SZ reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=000915.SZ reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=603323.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=600572.SH reason=below_min_lot
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
