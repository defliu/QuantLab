# Backtest Report -- rps_momentum_v3_balanced

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_134314_2d6b0d |
| run_started_at | 2026-08-14T13:43:14 |
| runtime_seconds | 738.369 |
| config_hash | 31a9548127caaa33f3d689d8db9ebc0732783f3442150f14adb94e6862affe0e |
| data_hash | 4afde87b52e2549bcc9e132684b741a49b0c753010c7f82476c2f1fd04859821 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -60.82% |
| annual_return | -9.02% |
| max_drawdown | -63.52% |
| sharpe | -0.468 |
| calmar | -0.142 |
| win_rate | 52.94% |
| n_trades | 509 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2020-11-03 unfilled_order code=000568.SZ reason=below_min_lot
[ERROR] 2020-12-04 unfilled_order code=601127.SH reason=limit_down_at_open
[ERROR] 2020-12-07 unfilled_order code=000768.SZ reason=below_min_lot
[ERROR] 2020-12-07 unfilled_order code=002801.SZ reason=below_min_lot
[ERROR] 2023-02-02 unfilled_order code=002410.SZ reason=below_min_lot
[ERROR] 2024-05-07 unfilled_order code=000952.SZ reason=limit_up_at_open
[ERROR] 2024-11-04 unfilled_order code=300339.SZ reason=below_min_lot
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
