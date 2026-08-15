# Backtest Report -- baseline

> WARNING **样本期警告**
>
> 本回测样本区间 `2022-01-04 ~ 2022-12-30`，约 11.5 个月（242 个交易日），**仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 数据补全后请重跑完整回测再做策略评估。

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_202143_62c2f3 |
| run_started_at | 2026-08-15T20:21:43 |
| runtime_seconds | 84.279 |
| config_hash |  |
| data_hash | 3bdba06bb652fd240c1f6b54c2eb3d3a4715e27db5709e9db6899303148e0811 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -19.46% |
| annual_return | -20.27% |
| max_drawdown | -20.34% |
| sharpe | -1.058 |
| calmar | -0.996 |
| win_rate | 36.79% |
| n_trades | 266 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 000028.SZ | 700 | 129.10 | 136.89 | 5451.88 |
| 688309.SH | 5600 | 18.20 | 19.80 | 8962.88 |
| 002152.SZ | 600 | 167.47 | 167.77 | 178.34 |
| 601163.SH | 7200 | 14.85 | 13.79 | -7645.38 |
| 000425.SZ | 400 | 219.99 | 207.44 | -5019.63 |
| 002327.SZ | 1100 | 86.59 | 81.64 | -5453.52 |
| 002515.SZ | 1900 | 54.54 | 53.37 | -2215.44 |
| 000423.SZ | 200 | 439.03 | 423.80 | -3046.05 |

## 关键日志摘录

```
[ERROR] 2022-12-26 unfilled_order code=601163.SH reason=below_min_lot
[ERROR] 2022-12-26 unfilled_order code=000028.SZ reason=below_min_lot
[ERROR] 2022-12-26 unfilled_order code=002152.SZ reason=below_min_lot
[ERROR] 2022-12-26 unfilled_order code=000425.SZ reason=below_min_lot
[ERROR] 2022-12-26 unfilled_order code=002327.SZ reason=below_min_lot
[ERROR] 2022-12-27 unfilled_order code=688309.SH reason=below_min_lot
[ERROR] 2022-12-27 unfilled_order code=000028.SZ reason=below_min_lot
[ERROR] 2022-12-27 unfilled_order code=002152.SZ reason=below_min_lot
[ERROR] 2022-12-27 unfilled_order code=000425.SZ reason=below_min_lot
[ERROR] 2022-12-27 unfilled_order code=002327.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 4958/5488 codes have data
- benchmark: disabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
