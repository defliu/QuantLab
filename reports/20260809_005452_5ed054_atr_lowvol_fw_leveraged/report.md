# Backtest Report -- atr_lowvol_fw_leveraged

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260809_005452_5ed054 |
| run_started_at | 2026-08-09T00:54:52 |
| runtime_seconds | 605.98 |
| config_hash | f002233fb5f44eac6d8c0c3dd9db3b7b7edba316e6bb335cd9d728771a33e238 |
| data_hash | 658f6109738361ba874b7865703c400d9ea0f89b5ae3acbe274cf364f676654d |
| universe_hash | a81a7b876e98494b0025afae303441c44e901728530637817e8eba0470207ecf |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 85.25% |
| annual_return | 24.81% |
| max_drawdown | -19.12% |
| sharpe | 0.984 |
| calmar | 1.297 |
| win_rate | 65.62% |
| n_trades | 3157 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 603167.SH | 16000 | 14.73 | 15.05 | 5034.93 |
| 002807.SZ | 7900 | 7.40 | 7.81 | 3242.41 |
| 601916.SH | 20900 | 4.05 | 4.38 | 6910.23 |
| 601939.SH | 2300 | 23.56 | 27.59 | 9282.91 |
| 001227.SZ | 37100 | 2.57 | 2.65 | 2977.68 |
| 601860.SH | 19300 | 3.33 | 3.41 | 1513.33 |
| 002948.SZ | 4400 | 7.67 | 8.60 | 4089.84 |
| 603128.SH | 10900 | 18.14 | 18.68 | 5864.54 |
| 600572.SH | 1600 | 74.01 | 77.61 | 5753.59 |
| 603213.SH | 8200 | 15.91 | 16.01 | 891.06 |
| 000333.SZ | 300 | 448.57 | 518.68 | 21033.16 |
| 601377.SH | 7000 | 16.86 | 17.25 | 2756.22 |
| 601333.SH | 39000 | 4.29 | 4.46 | 6474.00 |
| 000828.SZ | 3600 | 43.98 | 45.71 | 6217.37 |
| 600210.SH | 2500 | 65.44 | 66.50 | 2672.55 |

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
