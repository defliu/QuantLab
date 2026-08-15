# Backtest Report -- atr_10w_price50_vt0.1

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_111420_d8164f |
| run_started_at | 2026-08-15T11:14:20 |
| runtime_seconds | 813.434 |
| config_hash | 108a269c2d79b151d6effd30db747c6dfbe55cf2aed603fa61e156ce414b5c40 |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 70.56% |
| annual_return | 9.80% |
| max_drawdown | -15.21% |
| sharpe | 0.831 |
| calmar | 0.644 |
| win_rate | 71.21% |
| n_trades | 471 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 5000 | 4.16 | 4.01 | -748.08 |
| 601939.SH | 800 | 24.13 | 24.38 | 195.23 |
| 001227.SZ | 8300 | 2.60 | 2.46 | -1162.96 |
| 603323.SH | 2200 | 9.80 | 9.09 | -1547.04 |
| 600908.SH | 2900 | 7.47 | 6.94 | -1516.42 |

## 关键日志摘录

```
[ERROR] 2026-03-24 unfilled_order code=000915.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=002746.SZ reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=000915.SZ reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=601916.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=601939.SH reason=below_min_lot
[ERROR] 2026-04-23 unfilled_order code=001227.SZ reason=below_min_lot
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
