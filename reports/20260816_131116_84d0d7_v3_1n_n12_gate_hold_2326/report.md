# Backtest Report -- v3_1n_n12_gate_hold_2326

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_131116_84d0d7 |
| run_started_at | 2026-08-16T13:11:16 |
| runtime_seconds | 296.696 |
| config_hash |  |
| data_hash | b8a46d6ff84fd7665ddc6dd2beed13623a01cc52a2cbef9a85e229eae426e33b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 8.54% |
| 年化(线性) | 2.55% |
| 年化(CAGR) | 2.48% |
| max_drawdown | -20.75% |
| sharpe | 0.246 |
| calmar | 0.123 |
| 卡玛(CAGR) | 0.119 |
| win_rate | 44.27% |
| n_trades | 254 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 1800 | 57.85 | 51.44 | -11535.04 |
| 601665.SH | 13300 | 7.67 | 7.27 | -5280.31 |
| 601838.SH | 3600 | 26.23 | 25.47 | -2722.10 |
| 601939.SH | 3800 | 25.23 | 24.38 | -3245.18 |
| 601518.SH | 17300 | 5.47 | 4.93 | -9385.92 |
| 600999.SH | 1900 | 40.64 | 45.06 | 8389.36 |
| 600030.SH | 400 | 193.83 | 187.11 | -2687.01 |
| 601995.SH | 2400 | 37.02 | 36.60 | -1000.25 |

## 关键日志摘录

```
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2023-04-10 unfilled_order code=600620.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
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
