# Backtest Report -- v3_1n_n12_h60_s12_2326

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_125007_bb495b |
| run_started_at | 2026-08-16T12:50:07 |
| runtime_seconds | 289.622 |
| config_hash |  |
| data_hash | b8a46d6ff84fd7665ddc6dd2beed13623a01cc52a2cbef9a85e229eae426e33b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 25.39% |
| 年化(线性) | 7.59% |
| 年化(CAGR) | 7.00% |
| max_drawdown | -19.09% |
| sharpe | 0.497 |
| calmar | 0.398 |
| 卡玛(CAGR) | 0.367 |
| win_rate | 50.26% |
| n_trades | 372 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 605319.SH | 3000 | 39.38 | 34.16 | -15657.50 |
| 300050.SZ | 2000 | 57.85 | 51.44 | -12816.71 |
| 601838.SH | 4300 | 26.23 | 25.47 | -3251.39 |
| 000429.SZ | 1600 | 64.77 | 68.06 | 5256.57 |
| 601939.SH | 4200 | 25.23 | 24.38 | -3586.78 |
| 003013.SZ | 6200 | 17.68 | 17.98 | 1840.59 |
| 601518.SH | 19600 | 5.47 | 4.93 | -10633.76 |
| 600999.SH | 2400 | 41.83 | 45.06 | 7742.19 |
| 600030.SH | 500 | 193.83 | 187.11 | -3358.76 |
| 601995.SH | 2800 | 37.02 | 36.60 | -1166.96 |

## 关键日志摘录

```
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2023-04-10 unfilled_order code=600620.SH reason=below_min_lot
[ERROR] 2024-02-08 unfilled_order code=600690.SH reason=below_min_lot
[ERROR] 2024-05-08 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2024-09-04 unfilled_order code=000538.SZ reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=605319.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=605319.SH reason=suspended
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
