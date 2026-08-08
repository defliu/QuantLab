# Backtest Report -- atr_lowvol_fw_leveraged

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260809_004410_5b32d2 |
| run_started_at | 2026-08-09T00:44:10 |
| runtime_seconds | 620.981 |
| config_hash | 05687d976dc5a5e0249df06ba502d613e647a064e047376f298278d7faf7d47a |
| data_hash | 658f6109738361ba874b7865703c400d9ea0f89b5ae3acbe274cf364f676654d |
| universe_hash | a81a7b876e98494b0025afae303441c44e901728530637817e8eba0470207ecf |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 81.51% |
| annual_return | 23.72% |
| max_drawdown | -17.97% |
| sharpe | 1.015 |
| calmar | 1.320 |
| win_rate | 67.79% |
| n_trades | 3111 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 603167.SH | 15600 | 14.76 | 15.05 | 4514.34 |
| 002807.SZ | 7700 | 7.39 | 7.81 | 3180.38 |
| 601916.SH | 20400 | 4.05 | 4.38 | 6781.88 |
| 601939.SH | 2300 | 23.63 | 27.59 | 9125.11 |
| 001227.SZ | 36300 | 2.56 | 2.65 | 3259.41 |
| 601860.SH | 18900 | 3.32 | 3.41 | 1640.95 |
| 002948.SZ | 4300 | 7.65 | 8.60 | 4049.87 |
| 603128.SH | 10700 | 18.11 | 18.68 | 6046.40 |
| 600572.SH | 1500 | 73.80 | 77.61 | 5711.46 |
| 603213.SH | 8000 | 15.94 | 16.01 | 622.36 |
| 000333.SZ | 300 | 448.57 | 518.68 | 21033.16 |
| 601377.SH | 6900 | 16.66 | 17.25 | 4106.15 |
| 601333.SH | 38100 | 4.30 | 4.46 | 6208.55 |
| 000828.SZ | 3500 | 44.21 | 45.71 | 5253.35 |
| 600210.SH | 2500 | 65.24 | 66.50 | 3149.74 |

## 关键日志摘录

```
[ERROR] 2026-07-03 unfilled_order code=601860.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=002948.SZ reason=suspended
[ERROR] 2026-07-03 unfilled_order code=603128.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=600572.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=603213.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=000333.SZ reason=suspended
[ERROR] 2026-07-03 unfilled_order code=601377.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=601333.SH reason=suspended
[ERROR] 2026-07-03 unfilled_order code=000828.SZ reason=suspended
[ERROR] 2026-07-03 unfilled_order code=600210.SH reason=suspended
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5342/5471 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
