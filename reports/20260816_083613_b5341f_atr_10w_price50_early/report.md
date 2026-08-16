# Backtest Report -- atr_10w_price50_early

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_083613_b5341f |
| run_started_at | 2026-08-16T08:36:13 |
| runtime_seconds | 348.181 |
| config_hash | e497451ba6f6b58838a3df62791bab4d30a2f15aa9e3815239fe68c9beeb0d85 |
| data_hash | f6d71e1da7eb308544d86ae908329400e76640858fb58bc76e03bcaf5025e610 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 47.97% |
| annual_return | 12.44% |
| max_drawdown | -24.80% |
| sharpe | 0.601 |
| calmar | 0.502 |
| win_rate | 82.61% |
| n_trades | 350 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600905.SH | 3000 | 5.68 | 5.70 | 69.56 |
| 000989.SZ | 400 | 40.79 | 44.14 | 1342.46 |
| 300485.SZ | 300 | 45.25 | 50.60 | 1605.06 |
| 002788.SZ | 700 | 24.44 | 25.36 | 639.16 |
| 000705.SZ | 400 | 39.52 | 43.72 | 1678.17 |
| 000788.SZ | 600 | 25.47 | 28.00 | 1516.42 |
| 600513.SH | 300 | 56.36 | 60.90 | 1360.23 |

## 关键日志摘录

```
[ERROR] 2021-05-21 unfilled_order code=000685.SZ reason=below_min_lot
[ERROR] 2021-10-11 unfilled_order code=600270.SH reason=suspended
[ERROR] 2021-10-11 unfilled_order code=600068.SH reason=suspended
[ERROR] 2022-01-25 unfilled_order code=600755.SH reason=below_min_lot
[ERROR] 2022-01-25 unfilled_order code=002391.SZ reason=below_min_lot
[ERROR] 2022-04-06 unfilled_order code=600270.SH reason=suspended
[ERROR] 2022-04-06 unfilled_order code=600653.SH reason=below_min_lot
[ERROR] 2022-07-04 unfilled_order code=600064.SH reason=below_min_lot
[ERROR] 2022-09-20 unfilled_order code=000719.SZ reason=below_min_lot
[ERROR] 2022-10-11 unfilled_order code=600270.SH reason=suspended
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
