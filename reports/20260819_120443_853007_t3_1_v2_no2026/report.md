# Backtest Report -- t3_1_v2_no2026

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260819_120443_853007 |
| run_started_at | 2026-08-19T12:04:43 |
| runtime_seconds | 713.451 |
| config_hash |  |
| data_hash | 137755f2004afca0500043fc241e92c7c79f044b36996c2e18863f3bbf60f6c1 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 42.03% |
| 年化(线性) | 6.23% |
| 年化(CAGR) | 5.34% |
| max_drawdown | -24.24% |
| sharpe | 0.393 |
| calmar | 0.257 |
| 卡玛(CAGR) | 0.220 |
| win_rate | 46.48% |
| n_trades | 503 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601800.SH | 8000 | 13.04 | 12.06 | -7859.70 |
| 600903.SH | 11900 | 10.01 | 9.57 | -5147.48 |
| 601808.SH | 7100 | 16.76 | 17.21 | 3157.80 |
| 601952.SH | 8000 | 14.88 | 14.38 | -4055.93 |
| 601825.SH | 10800 | 10.99 | 12.32 | 14315.76 |
| 688253.SH | 3600 | 32.26 | 31.25 | -3612.20 |
| 600956.SH | 13400 | 9.08 | 8.11 | -12897.26 |
| 600054.SH | 2200 | 52.48 | 52.37 | -245.94 |
| 600830.SH | 1200 | 94.75 | 95.53 | 936.15 |

## 关键日志摘录

```
[ERROR] 2021-02-18 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-19 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-22 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-23 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-24 unfilled_order code=002735.SZ reason=suspended
[ERROR] 2021-02-25 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2021-02-26 unfilled_order code=002735.SZ reason=limit_down_at_open
[ERROR] 2023-06-13 unfilled_order code=600660.SH reason=below_min_lot
[ERROR] 2024-09-30 unfilled_order code=002970.SZ reason=limit_up_at_open
[ERROR] 2024-10-08 unfilled_order code=603337.SH reason=limit_up_at_open
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-16T15:21:30
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-14, 5820 codes
- universe_coverage: 5398/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
