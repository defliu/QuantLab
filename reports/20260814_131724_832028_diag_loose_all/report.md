# Backtest Report -- diag_loose_all

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_131724_832028 |
| run_started_at | 2026-08-14T13:17:24 |
| runtime_seconds | 156.696 |
| config_hash | 51e8daacc37df0363b3d0101047a53ec50818357cddd8155ec4d2b5468d1967d |
| data_hash | 9b3443eee71a58b6368d0f9b5323b7fa14a9bf5fa87e8d5bac03fca77d9724d7 |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | -43.58% |
| annual_return | -22.60% |
| max_drawdown | -45.61% |
| sharpe | -0.513 |
| calmar | -0.495 |
| win_rate | 52.84% |
| n_trades | 1122 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| (空) | | | | |

## 关键日志摘录

```
[ERROR] 2021-07-02 unfilled_order code=600687.SH reason=suspended
[ERROR] 2021-08-03 unfilled_order code=600687.SH reason=suspended
[ERROR] 2021-09-02 unfilled_order code=600687.SH reason=suspended
[ERROR] 2021-09-02 unfilled_order code=000016.SZ reason=suspended
[ERROR] 2021-10-11 unfilled_order code=600687.SH reason=suspended
[ERROR] 2021-10-11 unfilled_order code=600861.SH reason=suspended
[ERROR] 2021-11-02 unfilled_order code=600687.SH reason=suspended
[ERROR] 2021-11-02 unfilled_order code=000568.SZ reason=below_min_lot
[ERROR] 2021-11-02 unfilled_order code=603806.SH reason=below_min_lot
[ERROR] 2021-11-02 unfilled_order code=603338.SH reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-08T23:09:14
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-08-07, 5821 codes
- universe_coverage: 4648/5488 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
