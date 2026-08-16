# Backtest Report -- v3_1n_n8_gate_hold_1922

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260816_122435_863625 |
| run_started_at | 2026-08-16T12:24:35 |
| runtime_seconds | 280.551 |
| config_hash |  |
| data_hash | 466991c1092941731533db88d5cb915b91ae8ee3dc89b3c1546c28282c4d55df |
| universe_hash |  |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 57.12% |
| 年化(线性) | 14.81% |
| 年化(CAGR) | 12.43% |
| max_drawdown | -21.55% |
| sharpe | 0.736 |
| calmar | 0.687 |
| 卡玛(CAGR) | 0.577 |
| win_rate | 44.44% |
| n_trades | 216 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2019-12-17 unfilled_order code=000002.SZ reason=below_min_lot
[ERROR] 2020-02-04 unfilled_order code=603167.SH reason=limit_down_at_open
[ERROR] 2020-02-04 unfilled_order code=600193.SH reason=limit_down_at_open
[ERROR] 2020-04-23 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2020-06-02 unfilled_order code=600865.SH reason=limit_up_at_open
[ERROR] 2020-12-31 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-04 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-05 unfilled_order code=600212.SH reason=limit_up_at_open
[ERROR] 2021-01-06 unfilled_order code=600519.SH reason=below_min_lot
[ERROR] 2021-01-07 unfilled_order code=600519.SH reason=below_min_lot
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
