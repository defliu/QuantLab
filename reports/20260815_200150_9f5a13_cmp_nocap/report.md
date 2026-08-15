# Backtest Report -- baseline

> WARNING **样本期警告**
>
> 本回测样本区间 `2021-06-01 ~ 2022-12-30`，约 18.5 个月（388 个交易日），**仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 数据补全后请重跑完整回测再做策略评估。

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_200150_9f5a13 |
| run_started_at | 2026-08-15T20:01:50 |
| runtime_seconds | 131.183 |
| config_hash |  |
| data_hash | 48cbbe3e83ea4cd340b737728a3c83c9551845074c9b6d9ba0549e300c9a8c0f |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -2.18% |
| annual_return | -1.42% |
| max_drawdown | -18.39% |
| sharpe | 0.064 |
| calmar | -0.077 |
| win_rate | 50.94% |
| n_trades | 1041 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 2200 | 52.72 | 63.07 | 22769.59 |
| 002966.SZ | 13000 | 8.62 | 9.41 | 10182.59 |
| 000028.SZ | 900 | 130.35 | 136.89 | 5883.37 |
| 600998.SH | 8500 | 13.88 | 14.30 | 3563.18 |
| 600377.SH | 4700 | 25.65 | 26.54 | 4163.28 |
| 000425.SZ | 600 | 219.51 | 207.44 | -7242.68 |
| 002327.SZ | 1500 | 86.20 | 81.64 | -6851.33 |
| 002515.SZ | 2300 | 54.66 | 53.37 | -2964.27 |
| 000423.SZ | 200 | 439.03 | 423.80 | -3046.05 |

## 关键日志摘录

```
[ERROR] 2022-12-29 unfilled_order code=000423.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=002966.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=000028.SZ reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=600998.SH reason=below_min_lot
[ERROR] 2022-12-30 unfilled_order code=600377.SH reason=below_min_lot
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
