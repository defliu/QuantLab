# Backtest Report -- trae_uptrend_v2_gatehold

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260818_231743_c04bd8 |
| run_started_at | 2026-08-18T23:17:43 |
| runtime_seconds | 838.282 |
| config_hash |  |
| data_hash | 856906e6eb682c795789d8fd543c84c6e1b1084cbfa56ca58344b41de87e5e09 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 135.09% |
| 年化(线性) | 18.76% |
| 年化(CAGR) | 12.60% |
| max_drawdown | -29.85% |
| sharpe | 0.760 |
| calmar | 0.628 |
| 卡玛(CAGR) | 0.422 |
| win_rate | 46.05% |
| n_trades | 571 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 603156.SH | 800 | 122.51 | 193.82 | 57051.65 |
| 688345.SH | 2100 | 49.19 | 59.06 | 20714.02 |
| 002636.SZ | 1300 | 100.99 | 327.85 | 294920.87 |
| 300199.SZ | 700 | 174.15 | 183.93 | 6844.98 |
| 603311.SH | 6300 | 21.08 | 40.82 | 124396.05 |
| 600176.SH | 200 | 619.06 | 1423.57 | 160900.29 |
| 002317.SZ | 600 | 271.80 | 283.32 | 6910.29 |
| 600869.SH | 400 | 314.61 | 365.17 | 20223.49 |
| 600761.SH | 500 | 252.57 | 242.75 | -4905.94 |
| 600160.SH | 200 | 527.87 | 606.49 | 15725.54 |
| 002937.SZ | 2400 | 76.79 | 77.79 | 2404.82 |

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
