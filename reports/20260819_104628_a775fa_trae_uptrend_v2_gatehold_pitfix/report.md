# Backtest Report -- trae_uptrend_v2_gatehold_pitfix

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260819_104628_a775fa |
| run_started_at | 2026-08-19T10:46:28 |
| runtime_seconds | 804.153 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 113.87% |
| 年化(线性) | 15.81% |
| 年化(CAGR) | 11.13% |
| max_drawdown | -24.24% |
| sharpe | 0.684 |
| calmar | 0.652 |
| 卡玛(CAGR) | 0.459 |
| win_rate | 45.89% |
| n_trades | 573 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 603156.SH | 900 | 122.51 | 193.82 | 64183.11 |
| 688345.SH | 2200 | 49.19 | 59.06 | 21700.40 |
| 688163.SH | 4000 | 34.74 | 54.45 | 78812.25 |
| 600176.SH | 200 | 619.06 | 1423.57 | 160900.29 |
| 600179.SH | 15600 | 10.30 | 10.60 | 4682.89 |
| 600869.SH | 400 | 314.61 | 365.17 | 20223.49 |
| 600916.SH | 17100 | 9.16 | 8.04 | -19028.44 |
| 002937.SZ | 2300 | 76.79 | 77.79 | 2304.62 |
| 603995.SH | 4200 | 41.86 | 40.63 | -5146.00 |
| 002831.SZ | 1300 | 130.03 | 131.19 | 1515.52 |
| 002057.SZ | 2300 | 75.90 | 77.17 | 2923.47 |

## 关键日志摘录

```
[ERROR] 2021-02-19 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-22 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-23 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-24 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-25 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2021-02-26 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2023-06-13 unfilled_order code=600660.SH reason=below_min_lot
[ERROR] 2024-09-30 unfilled_order code=002970.SZ reason=limit_up_at_open
[ERROR] 2024-10-08 unfilled_order code=603337.SH reason=limit_up_at_open
[ERROR] 2026-01-07 unfilled_order code=000001.SZ reason=below_min_lot
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
