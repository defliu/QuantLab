# Backtest Report -- atr_lowvol_100w_100

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260814_163946_9c2b82 |
| run_started_at | 2026-08-14T16:39:46 |
| runtime_seconds | 897.13 |
| config_hash | bf2df0eea9f30352330370f5a7bff289807f95b2eff13d4f172cfc0859479c66 |
| data_hash | 4d8c3707eb098fc020d49534ee405e51bd31e71348039ee37742b00a528b0c7b |
| universe_hash | d12cc532e816c71ddfd04e81c7030b69023055c72a3c0429b208efb84c403687 |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 167.66% |
| annual_return | 23.28% |
| max_drawdown | -19.33% |
| sharpe | 0.828 |
| calmar | 1.204 |
| win_rate | 74.60% |
| n_trades | 13765 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 600068.SH | 400 | 48.85 | 63.07 | 5685.15 |
| 601033.SH | 4900 | 16.32 | 15.46 | -4227.98 |
| 603167.SH | 5200 | 14.85 | 14.18 | -3517.08 |
| 002807.SZ | 10500 | 7.46 | 7.11 | -3677.45 |
| 002839.SZ | 11300 | 7.09 | 6.60 | -5548.12 |
| 301309.SZ | 2100 | 32.79 | 33.67 | 1850.65 |
| 601916.SH | 18300 | 4.16 | 4.01 | -2860.09 |
| 601939.SH | 3000 | 24.46 | 24.38 | -263.61 |
| 001227.SZ | 30600 | 2.58 | 2.46 | -3662.58 |
| 603323.SH | 8000 | 9.85 | 9.09 | -6066.58 |
| 603128.SH | 4400 | 18.25 | 16.95 | -5727.40 |
| 600572.SH | 1000 | 73.72 | 69.38 | -4332.45 |
| 600908.SH | 10700 | 7.46 | 6.94 | -5561.68 |
| 600351.SH | 1400 | 53.97 | 48.87 | -7145.39 |
| 002958.SZ | 22400 | 3.58 | 3.35 | -5165.01 |
| 601860.SH | 23300 | 3.37 | 3.18 | -4373.83 |
| 600008.SH | 2400 | 31.81 | 29.15 | -6390.26 |
| 603213.SH | 4700 | 15.93 | 15.69 | -1113.85 |
| 601377.SH | 4500 | 16.55 | 16.86 | 1390.38 |
| 601528.SH | 10900 | 7.44 | 6.83 | -6628.26 |
| 601000.SH | 2400 | 33.25 | 30.82 | -5822.00 |
| 601333.SH | 18300 | 4.31 | 4.08 | -4238.81 |
| 000900.SZ | 1200 | 61.39 | 56.35 | -6051.00 |
| 600713.SH | 1200 | 63.72 | 62.50 | -1463.75 |
| 000828.SZ | 1700 | 44.10 | 42.33 | -3011.61 |
| 002948.SZ | 9600 | 7.79 | 7.76 | -306.53 |
| 600210.SH | 1100 | 66.94 | 62.45 | -4937.35 |
| 603111.SH | 4500 | 17.82 | 16.50 | -5921.06 |
| 000589.SZ | 2500 | 32.20 | 29.34 | -7134.35 |
| 300826.SZ | 2000 | 33.14 | 36.58 | 6882.56 |
| 601555.SH | 6700 | 11.27 | 11.20 | -428.34 |
| 601688.SH | 2700 | 25.99 | 28.40 | 6518.30 |
| 600033.SH | 2500 | 32.07 | 29.28 | -6968.05 |
| 600903.SH | 7200 | 11.11 | 10.53 | -4170.97 |
| 002752.SZ | 5700 | 13.51 | 12.31 | -6861.13 |
| 603259.SH | 200 | 258.58 | 305.57 | 9397.00 |

## 关键日志摘录

```
[ERROR] 2026-06-30 unfilled_order code=600713.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=000828.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=002948.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600210.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603111.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=300826.SZ reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601555.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=601688.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=600903.SH reason=suspended
[ERROR] 2026-06-30 unfilled_order code=603259.SH reason=suspended
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
