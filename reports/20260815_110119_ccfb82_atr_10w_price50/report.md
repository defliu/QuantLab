# Backtest Report -- atr_10w_price50

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_110119_ccfb82 |
| run_started_at | 2026-08-15T11:01:19 |
| runtime_seconds | 780.477 |
| config_hash | 6433eea2f74df59ee8e6e0e00e0b271ccc4ad350f3187ced24fbb4692a9fe003 |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 208.80% |
| annual_return | 28.99% |
| max_drawdown | -24.80% |
| sharpe | 0.894 |
| calmar | 1.169 |
| win_rate | 73.88% |
| n_trades | 651 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 15200 | 4.14 | 4.01 | -2045.21 |
| 601939.SH | 2500 | 23.99 | 24.38 | 969.43 |
| 001227.SZ | 25600 | 2.62 | 2.46 | -4014.41 |
| 603323.SH | 6600 | 9.93 | 9.09 | -5501.78 |
| 600908.SH | 8900 | 7.52 | 6.94 | -5113.09 |

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
