# Backtest Report -- v3_1n_n16_h60_s12_1922

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_132802_5fb287 |
| run_started_at | 2026-08-16T13:28:02 |
| runtime_seconds | 337.697 |
| config_hash |  |
| data_hash | 466991c1092941731533db88d5cb915b91ae8ee3dc89b3c1546c28282c4d55df |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 64.64% |
| 年化(线性) | 16.76% |
| 年化(CAGR) | 13.80% |
| max_drawdown | -18.51% |
| sharpe | 0.812 |
| calmar | 0.906 |
| 卡玛(CAGR) | 0.746 |
| win_rate | 44.19% |
| n_trades | 586 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 1700 | 52.72 | 63.07 | 17594.68 |
| 600941.SH | 1300 | 75.58 | 71.97 | -4698.93 |
| 002966.SZ | 9600 | 8.55 | 9.41 | 8254.16 |
| 600998.SH | 6800 | 13.71 | 14.30 | 3985.20 |
| 600901.SH | 14000 | 6.71 | 6.78 | 921.72 |
| 601607.SH | 500 | 184.34 | 183.39 | -475.09 |
| 603367.SH | 6700 | 14.04 | 13.71 | -2231.25 |
| 688425.SH | 24300 | 4.25 | 4.04 | -5103.75 |
| 600377.SH | 4000 | 25.60 | 26.54 | 3746.68 |
| 601988.SH | 15500 | 6.77 | 6.87 | 1553.91 |
| 601000.SH | 5900 | 17.48 | 17.02 | -2694.32 |
| 601169.SH | 5900 | 17.87 | 17.85 | -131.64 |
| 600704.SH | 1000 | 97.92 | 99.66 | 1742.51 |
| 000429.SZ | 3200 | 33.29 | 32.95 | -1076.98 |
| 002381.SZ | 2600 | 38.80 | 37.57 | -3188.24 |
| 002515.SZ | 1800 | 54.66 | 53.37 | -2319.87 |

## 关键日志摘录

```
[ERROR] 2022-12-20 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-21 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-22 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-23 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-23 unfilled_order code=002574.SZ reason=limit_up_at_open
[ERROR] 2022-12-26 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-27 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-28 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-29 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-12-30 unfilled_order code=600068.SH reason=suspended
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5005/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
