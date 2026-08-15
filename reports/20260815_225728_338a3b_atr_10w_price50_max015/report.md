# Backtest Report -- atr_10w_price50_max015

> WARNING **样本期警告**
>
> 本回测样本区间 `2019-01-02 ~ 2026-06-30`，约 86.4 个月（1815 个交易日），**仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 数据补全后请重跑完整回测再做策略评估。

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_225728_338a3b |
| run_started_at | 2026-08-15T22:57:28 |
| runtime_seconds | 990.022 |
| config_hash | 98d80a9c72deaaeb8ad21640bd7cbe5f9cf101a4b42c496d1bf2cbd6a04f7add |
| data_hash | 4d8c3707eb098fc020d49534ee405e51bd31e71348039ee37742b00a528b0c7b |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 237.60% |
| annual_return | 32.99% |
| max_drawdown | -20.05% |
| sharpe | 0.951 |
| calmar | 1.645 |
| win_rate | 74.80% |
| n_trades | 658 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 16600 | 4.14 | 4.01 | -2230.71 |
| 601939.SH | 2800 | 23.96 | 24.38 | 1152.44 |
| 001227.SZ | 27900 | 2.62 | 2.46 | -4368.58 |
| 603323.SH | 7200 | 9.92 | 9.09 | -5984.57 |
| 600908.SH | 9700 | 7.52 | 6.94 | -5550.32 |

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
- benchmark: disabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
