# Backtest Report -- v3_1n_n16_gate_hold_2326

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_135736_ded3b4 |
| run_started_at | 2026-08-16T13:57:36 |
| runtime_seconds | 324.972 |
| config_hash |  |
| data_hash | b8a46d6ff84fd7665ddc6dd2beed13623a01cc52a2cbef9a85e229eae426e33b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 5.08% |
| 年化(线性) | 1.52% |
| 年化(CAGR) | 1.49% |
| max_drawdown | -22.36% |
| sharpe | 0.177 |
| calmar | 0.068 |
| 卡玛(CAGR) | 0.067 |
| win_rate | 44.32% |
| n_trades | 340 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 1300 | 57.85 | 51.44 | -8330.86 |
| 601665.SH | 10100 | 7.47 | 7.27 | -1987.66 |
| 002458.SZ | 800 | 91.37 | 90.16 | -969.11 |
| 601838.SH | 2800 | 26.23 | 25.47 | -2117.19 |
| 301020.SZ | 2600 | 27.70 | 24.19 | -9142.98 |
| 601939.SH | 2900 | 25.23 | 24.38 | -2476.59 |
| 600900.SH | 600 | 105.61 | 101.04 | -2740.69 |
| 600377.SH | 1500 | 47.32 | 42.71 | -6904.82 |
| 601518.SH | 12100 | 5.47 | 4.93 | -6564.72 |
| 600999.SH | 1400 | 40.64 | 45.06 | 6181.63 |
| 600030.SH | 300 | 193.83 | 187.11 | -2015.26 |
| 601995.SH | 1700 | 37.02 | 36.60 | -708.51 |

## 关键日志摘录

```
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2023-04-10 unfilled_order code=600620.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2026-06-29 unfilled_order code=301020.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=301020.SZ reason=suspended
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
