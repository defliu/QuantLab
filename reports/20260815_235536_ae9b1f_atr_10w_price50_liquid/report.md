# Backtest Report -- atr_10w_price50_liquid

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_235536_ae9b1f |
| run_started_at | 2026-08-15T23:55:36 |
| runtime_seconds | 522.305 |
| config_hash | ee5df2c079cd3c28739dae1a5a3b4221831c6ef574e1b4dc53ffcaf5b76d4768 |
| data_hash | 9df026d6c59a4ca5f997e93f0bb3444fcfaa8772e77f2298ce4eb8310e768ce9 |
| universe_hash | 84bd01746c233f8bddd2c1d6f4215100c78aeebd639a2076d13922f99438b165 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 327.66% |
| annual_return | 45.49% |
| max_drawdown | -22.52% |
| sharpe | 1.077 |
| calmar | 2.020 |
| win_rate | 74.10% |
| n_trades | 669 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601916.SH | 21300 | 4.15 | 4.01 | -3007.39 |
| 601939.SH | 3500 | 24.01 | 24.38 | 1265.87 |
| 001227.SZ | 35600 | 2.62 | 2.46 | -5776.74 |
| 603323.SH | 9100 | 9.98 | 9.09 | -8053.42 |
| 600908.SH | 12200 | 7.56 | 6.94 | -7482.82 |

## 关键日志摘录

```
[ERROR] 2026-06-29 unfilled_order code=603323.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=600908.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601916.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=601939.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=001227.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603323.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600908.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601916.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601939.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=001227.SZ reason=suspended
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
