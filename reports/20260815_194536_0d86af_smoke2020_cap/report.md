# Backtest Report -- baseline

> WARNING **样本期警告**
>
> 本回测样本区间 `2020-01-02 ~ 2020-12-31`，约 11.6 个月（243 个交易日），**仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 数据补全后请重跑完整回测再做策略评估。

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_194536_0d86af |
| run_started_at | 2026-08-15T19:45:36 |
| runtime_seconds | 67.321 |
| config_hash |  |
| data_hash | 5da2fec42660861169466f76fbc1d3dc2c81d9857748f506d862958dce08907b |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -8.65% |
| annual_return | -8.97% |
| max_drawdown | -24.18% |
| sharpe | -0.262 |
| calmar | -0.371 |
| win_rate | 41.54% |
| n_trades | 300 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 601339.SH | 11900 | 9.82 | 9.46 | -4222.45 |
| 600019.SH | 7000 | 14.21 | 15.45 | 8650.66 |
| 600507.SH | 900 | 118.48 | 130.14 | 10499.69 |
| 002230.SZ | 200 | 535.36 | 550.45 | 3017.86 |
| 002499.SZ | 7400 | 10.54 | 16.61 | 44862.37 |
| 600098.SH | 3200 | 34.25 | 35.42 | 3736.58 |
| 600301.SH | 9600 | 11.84 | 11.80 | -321.87 |
| 002608.SZ | 5800 | 18.83 | 19.98 | 6661.39 |

## 关键日志摘录

```
[ERROR] 2020-12-15 unfilled_order code=601997.SH reason=below_min_lot
[ERROR] 2020-12-15 unfilled_order code=600507.SH reason=below_min_lot
[ERROR] 2020-12-15 unfilled_order code=002230.SZ reason=below_min_lot
[ERROR] 2020-12-21 unfilled_order code=600507.SH reason=below_min_lot
[ERROR] 2020-12-21 unfilled_order code=600098.SH reason=below_min_lot
[ERROR] 2020-12-21 unfilled_order code=002230.SZ reason=below_min_lot
[ERROR] 2020-12-21 unfilled_order code=600249.SH reason=below_min_lot
[ERROR] 2020-12-25 unfilled_order code=601339.SH reason=below_min_lot
[ERROR] 2020-12-25 unfilled_order code=600507.SH reason=below_min_lot
[ERROR] 2020-12-25 unfilled_order code=002230.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 4163/5488 codes have data
- benchmark: disabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
