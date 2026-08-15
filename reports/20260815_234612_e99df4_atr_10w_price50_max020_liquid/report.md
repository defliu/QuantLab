# Backtest Report -- atr_10w_price50_max020_liquid

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_234612_e99df4 |
| run_started_at | 2026-08-15T23:46:12 |
| runtime_seconds | 534.29 |
| config_hash | c41ba3fe1694e6d980af341e8f4ddb4d9529388f458b9b21ab4731ac28265309 |
| data_hash | 9df026d6c59a4ca5f997e93f0bb3444fcfaa8772e77f2298ce4eb8310e768ce9 |
| universe_hash | 84bd01746c233f8bddd2c1d6f4215100c78aeebd639a2076d13922f99438b165 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 307.87% |
| annual_return | 42.75% |
| max_drawdown | -22.96% |
| sharpe | 1.052 |
| calmar | 1.861 |
| win_rate | 73.31% |
| n_trades | 672 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 20100 | 4.14 | 4.01 | -2715.93 |
| 601939.SH | 3300 | 24.01 | 24.38 | 1219.56 |
| 001227.SZ | 33800 | 2.62 | 2.46 | -5470.83 |
| 603323.SH | 8700 | 9.94 | 9.09 | -7368.93 |
| 600908.SH | 11700 | 7.53 | 6.94 | -6904.30 |

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
- universe_coverage: 3754/3757 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
