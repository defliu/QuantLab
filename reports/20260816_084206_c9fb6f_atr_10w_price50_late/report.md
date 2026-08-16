# Backtest Report -- atr_10w_price50_late

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_084206_c9fb6f |
| run_started_at | 2026-08-16T08:42:06 |
| runtime_seconds | 361.713 |
| config_hash | e8464cf310d816a21a6a53404666ccb7cdff92aea2bce8f3b6fccf725b850264 |
| data_hash | 3fe318fcdead1223c75f359f51f9f4ac4f64944410ae9ff07c6a1cbd80326da4 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 105.45% |
| annual_return | 31.52% |
| max_drawdown | -14.71% |
| sharpe | 1.254 |
| calmar | 2.143 |
| win_rate | 64.52% |
| n_trades | 285 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 10100 | 4.14 | 4.01 | -1358.49 |
| 601939.SH | 1700 | 23.96 | 24.38 | 714.32 |
| 001227.SZ | 17000 | 2.62 | 2.46 | -2661.79 |
| 603323.SH | 4400 | 9.92 | 9.09 | -3651.88 |
| 600908.SH | 5900 | 7.52 | 6.94 | -3387.37 |

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
- universe_coverage: 5354/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
