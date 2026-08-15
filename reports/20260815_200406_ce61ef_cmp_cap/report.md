# Backtest Report -- baseline

> WARNING **样本期警告**
>
> 本回测样本区间 `2021-06-01 ~ 2022-12-30`，约 18.5 个月（388 个交易日），**仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 数据补全后请重跑完整回测再做策略评估。

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_200406_ce61ef |
| run_started_at | 2026-08-15T20:04:06 |
| runtime_seconds | 133.524 |
| config_hash |  |
| data_hash | 48cbbe3e83ea4cd340b737728a3c83c9551845074c9b6d9ba0549e300c9a8c0f |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -7.47% |
| annual_return | -4.85% |
| max_drawdown | -20.89% |
| sharpe | -0.093 |
| calmar | -0.232 |
| win_rate | 51.94% |
| n_trades | 1009 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 2200 | 52.72 | 63.07 | 22769.59 |
| 002966.SZ | 12300 | 8.62 | 9.41 | 9651.02 |
| 000028.SZ | 900 | 130.03 | 136.89 | 6175.10 |
| 600998.SH | 8000 | 13.87 | 14.30 | 3425.37 |
| 688009.SH | 21900 | 5.06 | 5.33 | 5777.61 |
| 000425.SZ | 600 | 217.87 | 207.44 | -6259.51 |
| 002327.SZ | 1400 | 86.18 | 81.64 | -6358.16 |
| 002515.SZ | 2200 | 54.66 | 53.37 | -2835.39 |
| 000423.SZ | 200 | 439.03 | 423.80 | -3046.05 |

## 关键日志摘录

```
[ERROR] 2022-12-29 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-29 unfilled_order code=002327.SZ reason=below_min_lot
[ERROR] 2022-12-29 unfilled_order code=000423.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=002966.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=000028.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=000425.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-30 unfilled_order code=002327.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=002515.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=000423.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 4967/5488 codes have data
- benchmark: disabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
