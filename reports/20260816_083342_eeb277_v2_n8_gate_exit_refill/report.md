# Backtest Report -- v2_n8_gate_exit_refill

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_083342_eeb277 |
| run_started_at | 2026-08-16T08:33:42 |
| runtime_seconds | 744.192 |
| config_hash |  |
| data_hash | 5b2ffa275dc535dbc33dd658bb3ec2dedc76970969404a423bb7e627c3de8667 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -31.99% |
| annual_return | -4.44% |
| max_drawdown | -41.21% |
| sharpe | -0.318 |
| calmar | -0.108 |
| win_rate | 40.10% |
| n_trades | 1214 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600999.SH | 2100 | 40.64 | 45.06 | 9272.45 |
| 002668.SZ | 1700 | 50.25 | 55.49 | 8897.05 |
| 601328.SH | 4600 | 19.07 | 17.72 | -6243.36 |
| 601398.SH | 4500 | 19.26 | 18.27 | -4476.59 |
| 600919.SH | 4400 | 19.75 | 18.55 | -5260.72 |
| 601838.SH | 3100 | 28.26 | 25.47 | -8639.66 |
| 601988.SH | 5500 | 15.78 | 14.88 | -4940.47 |
| 000795.SZ | 1900 | 44.66 | 38.41 | -11881.67 |

## 关键日志摘录

```
[ERROR] 2025-11-28 unfilled_order code=600690.SH reason=below_min_lot
[ERROR] 2025-12-04 unfilled_order code=000333.SZ reason=below_min_lot
[ERROR] 2025-12-04 unfilled_order code=600795.SH reason=below_min_lot
[ERROR] 2025-12-05 unfilled_order code=600795.SH reason=below_min_lot
[ERROR] 2026-05-12 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-05-13 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2026-06-16 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-18 unfilled_order code=000001.SZ reason=below_min_lot
[ERROR] 2026-06-29 unfilled_order code=000795.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=000795.SZ reason=suspended
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
