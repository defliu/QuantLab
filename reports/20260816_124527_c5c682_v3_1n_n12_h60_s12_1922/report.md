# Backtest Report -- v3_1n_n12_h60_s12_1922

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_124527_c5c682 |
| run_started_at | 2026-08-16T12:45:27 |
| runtime_seconds | 275.699 |
| config_hash |  |
| data_hash | 466991c1092941731533db88d5cb915b91ae8ee3dc89b3c1546c28282c4d55df |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 66.98% |
| 年化(线性) | 17.36% |
| 年化(CAGR) | 14.22% |
| max_drawdown | -21.97% |
| sharpe | 0.769 |
| calmar | 0.790 |
| 卡玛(CAGR) | 0.647 |
| win_rate | 43.35% |
| n_trades | 454 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600941.SH | 1500 | 75.58 | 71.97 | -5421.84 |
| 002966.SZ | 12800 | 8.55 | 9.41 | 11005.54 |
| 600998.SH | 8400 | 13.71 | 14.30 | 4922.90 |
| 600901.SH | 19200 | 6.71 | 6.78 | 1264.07 |
| 601607.SH | 700 | 184.34 | 183.39 | -665.12 |
| 603367.SH | 9100 | 14.04 | 13.71 | -3030.51 |
| 688425.SH | 32600 | 4.25 | 4.04 | -6847.01 |
| 600377.SH | 5400 | 25.60 | 26.54 | 5058.01 |
| 601988.SH | 20800 | 6.77 | 6.87 | 2085.24 |
| 601000.SH | 8000 | 17.48 | 17.02 | -3653.31 |
| 600704.SH | 1400 | 97.92 | 99.66 | 2439.52 |
| 000429.SZ | 4300 | 33.29 | 32.95 | -1447.20 |

## 关键日志摘录

```
[ERROR] 2020-02-04 unfilled_order code=603192.SH reason=limit_down_at_open
[ERROR] 2020-04-30 unfilled_order code=000538.SZ reason=below_min_lot
[ERROR] 2020-05-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2020-06-02 unfilled_order code=600865.SH reason=limit_up_at_open
[ERROR] 2020-11-05 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2021-01-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-05-10 unfilled_order code=002716.SZ reason=limit_up_at_open
[ERROR] 2021-07-29 unfilled_order code=603115.SH reason=limit_up_at_open
[ERROR] 2021-11-01 unfilled_order code=600887.SH reason=below_min_lot
[ERROR] 2022-06-27 unfilled_order code=600519.SH reason=below_min_lot
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
