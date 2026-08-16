# Backtest Report -- v3_1n_n8_gate_hold_2326

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_122919_d5b72d |
| run_started_at | 2026-08-16T12:29:19 |
| runtime_seconds | 298.896 |
| config_hash |  |
| data_hash | b8a46d6ff84fd7665ddc6dd2beed13623a01cc52a2cbef9a85e229eae426e33b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 8.11% |
| 年化(线性) | 2.43% |
| 年化(CAGR) | 2.36% |
| max_drawdown | -21.75% |
| sharpe | 0.229 |
| calmar | 0.112 |
| 卡玛(CAGR) | 0.108 |
| win_rate | 46.07% |
| n_trades | 171 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 2700 | 57.85 | 51.44 | -17302.55 |
| 601665.SH | 20000 | 7.67 | 7.27 | -7940.32 |
| 601838.SH | 5700 | 26.23 | 25.47 | -4309.99 |
| 601939.SH | 5500 | 25.23 | 24.38 | -4696.98 |
| 601518.SH | 23200 | 5.47 | 4.93 | -12586.90 |
| 600030.SH | 600 | 193.83 | 187.11 | -4030.51 |
| 601995.SH | 3600 | 37.02 | 36.60 | -1500.38 |

## 关键日志摘录

```
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
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
