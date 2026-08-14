# Backtest Report -- rps_momentum_v1_smoke

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_123912_30c3b9 |
| run_started_at | 2026-08-14T12:39:12 |
| runtime_seconds | 208.236 |
| config_hash | 7f003cd0f547e04fa1834948388051754f3ba8b729589900fbc638f406ba4152 |
| data_hash | f9c79e9cc40553728419c794732ccb352a02636e03fe53081a9888c4319d7270 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -23.17% |
| annual_return | -12.06% |
| max_drawdown | -37.83% |
| sharpe | -0.319 |
| calmar | -0.319 |
| win_rate | 39.73% |
| n_trades | 526 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 301109.SZ | 27600 | 30.63 | 28.29 | -64708.20 |

## 关键日志摘录

```
[ERROR] 2024-04-02 unfilled_order code=603663.SH reason=limit_up_at_open
[ERROR] 2024-04-03 unfilled_order code=002769.SZ reason=limit_down_at_open
[ERROR] 2024-04-08 unfilled_order code=002840.SZ reason=below_min_lot
[ERROR] 2024-04-08 unfilled_order code=301091.SZ reason=below_min_lot
[ERROR] 2024-04-08 unfilled_order code=001696.SZ reason=below_min_lot
[ERROR] 2024-04-09 unfilled_order code=002769.SZ reason=limit_down_at_open
[ERROR] 2024-10-09 unfilled_order code=300896.SZ reason=below_min_lot
[ERROR] 2024-10-10 unfilled_order code=000717.SZ reason=limit_down_at_open
[ERROR] 2024-10-14 unfilled_order code=300468.SZ reason=limit_up_at_open
[ERROR] 2024-12-03 unfilled_order code=600889.SH reason=limit_up_at_open
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5229/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
