# Backtest Report -- atr_10w_price50_aplus_momval

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_172639_f4930b |
| run_started_at | 2026-08-15T17:26:39 |
| runtime_seconds | 814.847 |
| config_hash | e1587e6bde53ebf2899ebd5ea25b064fc892aaf125c2712ff367fca77ea65da8 |
| data_hash | 4d8c3707eb098fc020d49534ee405e51bd31e71348039ee37742b00a528b0c7b |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -30.74% |
| annual_return | -4.27% |
| max_drawdown | -54.07% |
| sharpe | -0.061 |
| calmar | -0.079 |
| win_rate | 67.18% |
| n_trades | 607 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 001227.SZ | 7000 | 2.63 | 2.46 | -1195.60 |
| 002958.SZ | 5100 | 3.63 | 3.35 | -1457.14 |
| 601916.SH | 4300 | 4.13 | 4.01 | -534.00 |
| 603993.SH | 200 | 67.28 | 64.43 | -570.15 |

## 关键日志摘录

```
[ERROR] 2025-10-10 unfilled_order code=003010.SZ reason=below_min_lot
[ERROR] 2025-10-10 unfilled_order code=600016.SH reason=below_min_lot
[ERROR] 2025-10-10 unfilled_order code=300100.SZ reason=below_min_lot
[ERROR] 2026-01-06 unfilled_order code=002602.SZ reason=below_min_lot
[ERROR] 2026-01-06 unfilled_order code=300539.SZ reason=below_min_lot
[ERROR] 2026-02-03 unfilled_order code=000506.SZ reason=limit_down_at_open
[ERROR] 2026-03-24 unfilled_order code=600782.SH reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=002549.SZ reason=below_min_lot
[ERROR] 2026-04-15 unfilled_order code=603993.SH reason=below_min_lot
[ERROR] 2026-04-15 unfilled_order code=600649.SH reason=below_min_lot
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
