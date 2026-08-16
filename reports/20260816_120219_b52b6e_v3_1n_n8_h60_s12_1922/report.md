# Backtest Report -- v3_1n_n8_h60_s12_1922

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_120219_b52b6e |
| run_started_at | 2026-08-16T12:02:19 |
| runtime_seconds | 305.146 |
| config_hash |  |
| data_hash | 466991c1092941731533db88d5cb915b91ae8ee3dc89b3c1546c28282c4d55df |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 39.02% |
| 年化(线性) | 10.12% |
| 年化(CAGR) | 8.92% |
| max_drawdown | -26.84% |
| sharpe | 0.513 |
| calmar | 0.377 |
| 卡玛(CAGR) | 0.332 |
| win_rate | 43.40% |
| n_trades | 310 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 002966.SZ | 18900 | 8.55 | 9.41 | 16250.37 |
| 600998.SH | 8300 | 13.71 | 14.30 | 4864.29 |
| 600901.SH | 23200 | 6.71 | 6.78 | 1527.42 |
| 601607.SH | 800 | 184.34 | 183.39 | -760.14 |
| 688425.SH | 40400 | 4.25 | 4.04 | -8485.25 |
| 600377.SH | 6700 | 25.60 | 26.54 | 6275.68 |
| 601988.SH | 25700 | 6.77 | 6.87 | 2576.48 |
| 000429.SZ | 5300 | 33.29 | 32.95 | -1783.75 |

## 关键日志摘录

```
[ERROR] 2019-11-27 unfilled_order code=600309.SH reason=below_min_lot
[ERROR] 2020-02-04 unfilled_order code=603192.SH reason=limit_down_at_open
[ERROR] 2020-04-30 unfilled_order code=000538.SZ reason=below_min_lot
[ERROR] 2020-05-07 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2020-06-02 unfilled_order code=600865.SH reason=limit_up_at_open
[ERROR] 2020-11-05 unfilled_order code=000651.SZ reason=below_min_lot
[ERROR] 2021-05-10 unfilled_order code=002716.SZ reason=limit_up_at_open
[ERROR] 2021-07-29 unfilled_order code=603115.SH reason=limit_up_at_open
[ERROR] 2021-11-01 unfilled_order code=600887.SH reason=below_min_lot
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
