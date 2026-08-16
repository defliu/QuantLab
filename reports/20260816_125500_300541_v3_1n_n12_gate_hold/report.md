# Backtest Report -- v3_1n_n12_gate_hold

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_125500_300541 |
| run_started_at | 2026-08-16T12:55:00 |
| runtime_seconds | 669.836 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 64.61% |
| 年化(线性) | 8.97% |
| 年化(CAGR) | 7.17% |
| max_drawdown | -21.54% |
| sharpe | 0.532 |
| calmar | 0.416 |
| 卡玛(CAGR) | 0.333 |
| win_rate | 44.71% |
| n_trades | 578 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 300050.SZ | 2700 | 57.85 | 51.44 | -17302.55 |
| 601665.SH | 19800 | 7.67 | 7.27 | -7860.92 |
| 601838.SH | 5700 | 26.23 | 25.47 | -4309.99 |
| 601939.SH | 5600 | 25.23 | 24.38 | -4782.38 |
| 601518.SH | 23400 | 5.47 | 4.93 | -12695.41 |
| 600999.SH | 3200 | 40.64 | 45.06 | 14129.45 |
| 600030.SH | 600 | 193.83 | 187.11 | -4030.51 |
| 601995.SH | 3700 | 37.02 | 36.60 | -1542.06 |

## 关键日志摘录

```
[ERROR] 2020-06-02 unfilled_order code=600865.SH reason=limit_up_at_open
[ERROR] 2020-11-05 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2020-12-31 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-04 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-05 unfilled_order code=600212.SH reason=limit_up_at_open
[ERROR] 2021-01-06 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2023-01-05 unfilled_order code=600612.SH reason=below_min_lot
[ERROR] 2023-04-10 unfilled_order code=600620.SH reason=below_min_lot
[ERROR] 2025-04-07 unfilled_order code=002938.SZ reason=limit_down_at_open
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5443/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
