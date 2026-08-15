# Backtest Report -- baseline

> WARNING **样本期警告**
>
> 本回测样本区间 `2022-01-04 ~ 2022-12-30`，约 11.5 个月（242 个交易日），**仅用于 MVP 管线验证**，**不可作为策略最终定论**。
>
> 数据补全后请重跑完整回测再做策略评估。

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_202402_a16424 |
| run_started_at | 2026-08-15T20:24:02 |
| runtime_seconds | 85.362 |
| config_hash |  |
| data_hash | 3bdba06bb652fd240c1f6b54c2eb3d3a4715e27db5709e9db6899303148e0811 |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -16.13% |
| annual_return | -16.80% |
| max_drawdown | -20.93% |
| sharpe | -0.817 |
| calmar | -0.803 |
| win_rate | 35.19% |
| n_trades | 100 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 002966.SZ | 600 | 8.55 | 9.41 | 515.88 |
| 600846.SH | 1000 | 147.99 | 152.38 | 4391.78 |
| 688009.SH | 1100 | 5.02 | 5.33 | 335.66 |
| 000623.SZ | 300 | 338.44 | 327.10 | -3399.99 |
| 688425.SH | 38400 | 4.25 | 4.04 | -8065.19 |
| 600377.SH | 6300 | 25.60 | 26.54 | 5901.01 |
| 601988.SH | 24200 | 6.77 | 6.87 | 2426.10 |
| 600966.SH | 1300 | 58.89 | 64.71 | 7569.72 |

## 关键日志摘录

```
[ERROR] 2022-02-08 unfilled_order code=600377.SH reason=below_min_lot
[ERROR] 2022-04-26 unfilled_order code=603116.SH reason=limit_down_at_open
[ERROR] 2022-07-04 unfilled_order code=600674.SH reason=below_min_lot
[ERROR] 2022-07-05 unfilled_order code=600674.SH reason=below_min_lot
[ERROR] 2022-12-09 unfilled_order code=600611.SH reason=below_min_lot
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
