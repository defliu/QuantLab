# Backtest Report -- rps_momentum_v1_baseline

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_125257_7470e9 |
| run_started_at | 2026-08-14T12:52:57 |
| runtime_seconds | 676.451 |
| config_hash | 409543f32d5f168c81de59aa9f93acd980bddde7908e759f8447e4321f72a76b |
| data_hash | 4afde87b52e2549bcc9e132684b741a49b0c753010c7f82476c2f1fd04859821 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -49.68% |
| annual_return | -7.37% |
| max_drawdown | -51.47% |
| sharpe | -0.665 |
| calmar | -0.143 |
| win_rate | 46.15% |
| n_trades | 188 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2020-11-03 unfilled_order code=000568.SZ reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5408/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
