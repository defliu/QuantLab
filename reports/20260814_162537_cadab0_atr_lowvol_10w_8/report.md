# Backtest Report -- atr_lowvol_10w_8

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_162537_cadab0 |
| run_started_at | 2026-08-14T16:25:37 |
| runtime_seconds | 805.803 |
| config_hash | 3502aa97bdc65835705379206fee5789dc1e828a3c025071da43f9cc32844dbe |
| data_hash | 4d8c3707eb098fc020d49534ee405e51bd31e71348039ee37742b00a528b0c7b |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 182.94% |
| annual_return | 25.40% |
| max_drawdown | -24.45% |
| sharpe | 0.840 |
| calmar | 1.039 |
| win_rate | 73.86% |
| n_trades | 631 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 13900 | 4.14 | 4.01 | -1873.15 |
| 601939.SH | 2300 | 23.84 | 24.38 | 1240.05 |
| 001227.SZ | 23200 | 2.62 | 2.46 | -3786.52 |
| 603323.SH | 6100 | 9.89 | 9.09 | -4876.21 |
| 600908.SH | 8100 | 7.53 | 6.94 | -4745.77 |

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
