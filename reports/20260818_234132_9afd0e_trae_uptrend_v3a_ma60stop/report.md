# Backtest Report -- trae_uptrend_v3a_ma60stop

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260818_234132_9afd0e |
| run_started_at | 2026-08-18T23:41:32 |
| runtime_seconds | 804.41 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 178.16% |
| 年化(线性) | 24.74% |
| 年化(CAGR) | 15.26% |
| max_drawdown | -18.47% |
| sharpe | 0.892 |
| calmar | 1.339 |
| 卡玛(CAGR) | 0.826 |
| win_rate | 33.98% |
| n_trades | 1224 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600105.SH | 400 | 359.28 | 799.27 | 175996.19 |
| 002636.SZ | 1300 | 100.99 | 327.85 | 294920.87 |
| 301188.SZ | 2800 | 24.97 | 69.32 | 124196.92 |
| 600176.SH | 200 | 652.16 | 1423.57 | 154281.37 |
| 600888.SH | 800 | 127.43 | 224.37 | 77550.09 |
| 002317.SZ | 600 | 271.80 | 283.32 | 6910.29 |
| 605580.SH | 4600 | 39.69 | 44.87 | 23856.52 |
| 600869.SH | 400 | 314.61 | 365.17 | 20223.49 |
| 300304.SZ | 700 | 158.31 | 155.07 | -2266.76 |
| 002937.SZ | 1000 | 76.47 | 77.79 | 1321.15 |
| 002057.SZ | 1000 | 74.59 | 77.17 | 2576.47 |
| 301055.SZ | 1600 | 48.88 | 45.02 | -6169.91 |

## 关键日志摘录

```
[ERROR] 2024-10-08 unfilled_order code=603337.SH reason=limit_up_at_open
[ERROR] 2024-12-24 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2024-12-27 unfilled_order code=600391.SH reason=limit_up_at_open
[ERROR] 2025-11-25 unfilled_order code=002271.SZ reason=below_min_lot
[ERROR] 2026-03-04 unfilled_order code=300438.SZ reason=below_min_lot
[ERROR] 2026-06-03 unfilled_order code=603039.SH reason=below_min_lot
[ERROR] 2026-06-05 unfilled_order code=601918.SH reason=below_min_lot
[ERROR] 2026-06-22 unfilled_order code=600761.SH reason=below_min_lot
[ERROR] 2026-06-24 unfilled_order code=600160.SH reason=below_min_lot
[ERROR] 2026-06-24 unfilled_order code=002056.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-16T15:21:30
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-14, 5820 codes
- universe_coverage: 5433/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
