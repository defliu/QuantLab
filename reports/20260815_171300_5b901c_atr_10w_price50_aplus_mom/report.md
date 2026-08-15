# Backtest Report -- atr_10w_price50_aplus_mom

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260815_171300_5b901c |
| run_started_at | 2026-08-15T17:13:00 |
| runtime_seconds | 813.994 |
| config_hash | 24aae32dfe3a3c6482d285265d5932c483e38ce1d7c1b61df87ddf9015d2d006 |
| data_hash | 4d8c3707eb098fc020d49534ee405e51bd31e71348039ee37742b00a528b0c7b |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -51.64% |
| annual_return | -7.17% |
| max_drawdown | -66.76% |
| sharpe | -0.101 |
| calmar | -0.107 |
| win_rate | 61.68% |
| n_trades | 319 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2025-10-10 unfilled_order code=605488.SH reason=below_min_lot
[ERROR] 2026-01-06 unfilled_order code=002602.SZ reason=below_min_lot
[ERROR] 2026-01-06 unfilled_order code=300539.SZ reason=below_min_lot
[ERROR] 2026-01-06 unfilled_order code=605100.SH reason=below_min_lot
[ERROR] 2026-01-06 unfilled_order code=000880.SZ reason=below_min_lot
[ERROR] 2026-03-24 unfilled_order code=603256.SH reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=002549.SZ reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=001212.SZ reason=below_min_lot
[ERROR] 2026-04-02 unfilled_order code=605298.SH reason=below_min_lot
[ERROR] 2026-04-29 unfilled_order code=603993.SH reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 5443/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
