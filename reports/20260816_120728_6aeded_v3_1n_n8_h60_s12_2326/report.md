# Backtest Report -- v3_1n_n8_h60_s12_2326

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_120728_6aeded |
| run_started_at | 2026-08-16T12:07:28 |
| runtime_seconds | 297.956 |
| config_hash |  |
| data_hash | b8a46d6ff84fd7665ddc6dd2beed13623a01cc52a2cbef9a85e229eae426e33b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 13.43% |
| 年化(线性) | 4.01% |
| 年化(CAGR) | 3.84% |
| max_drawdown | -24.78% |
| sharpe | 0.304 |
| calmar | 0.162 |
| 卡玛(CAGR) | 0.155 |
| win_rate | 48.84% |
| n_trades | 251 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 2900 | 57.85 | 51.44 | -18584.22 |
| 601838.SH | 6200 | 26.23 | 25.47 | -4688.06 |
| 601939.SH | 5800 | 25.23 | 24.38 | -4953.18 |
| 600900.SH | 1100 | 105.61 | 101.04 | -5024.59 |
| 601518.SH | 27300 | 5.47 | 4.93 | -14811.31 |
| 600030.SH | 700 | 193.83 | 187.11 | -4702.27 |
| 601995.SH | 3800 | 37.02 | 36.60 | -1583.73 |

## 关键日志摘录

```
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2024-05-08 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
[ERROR] 2025-11-19 unfilled_order code=600887.SH reason=below_min_lot
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
