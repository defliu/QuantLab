# Backtest Report -- v3_1n_n16_h60_s12_2326

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_133344_ad68e6 |
| run_started_at | 2026-08-16T13:33:44 |
| runtime_seconds | 341.59 |
| config_hash |  |
| data_hash | b8a46d6ff84fd7665ddc6dd2beed13623a01cc52a2cbef9a85e229eae426e33b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 13.16% |
| 年化(线性) | 3.93% |
| 年化(CAGR) | 3.76% |
| max_drawdown | -22.01% |
| sharpe | 0.312 |
| calmar | 0.179 |
| 卡玛(CAGR) | 0.171 |
| win_rate | 48.03% |
| n_trades | 495 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 605319.SH | 2100 | 39.38 | 34.16 | -10960.25 |
| 300050.SZ | 1400 | 57.85 | 51.44 | -8971.69 |
| 601665.SH | 10400 | 7.67 | 7.27 | -4128.97 |
| 601838.SH | 3000 | 26.23 | 25.47 | -2268.41 |
| 000429.SZ | 1200 | 64.77 | 68.06 | 3942.43 |
| 601939.SH | 3000 | 25.23 | 24.38 | -2561.99 |
| 600900.SH | 700 | 105.61 | 101.04 | -3197.47 |
| 601518.SH | 13900 | 5.47 | 4.93 | -7541.29 |
| 601988.SH | 3200 | 15.78 | 14.88 | -2874.45 |
| 600999.SH | 1500 | 40.64 | 45.06 | 6623.18 |
| 000795.SZ | 1600 | 44.66 | 38.41 | -10005.62 |
| 600030.SH | 300 | 193.83 | 187.11 | -2015.26 |
| 601995.SH | 1900 | 37.02 | 36.60 | -791.87 |

## 关键日志摘录

```
[ERROR] 2024-09-04 unfilled_order code=000538.SZ reason=below_min_lot
[ERROR] 2024-09-19 unfilled_order code=600406.SH reason=below_min_lot
[ERROR] 2025-03-03 unfilled_order code=600031.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-06-15 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=605319.SH reason=suspended
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=605319.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=000795.SZ reason=suspended
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
