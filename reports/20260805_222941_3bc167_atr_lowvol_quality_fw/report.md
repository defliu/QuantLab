# Backtest Report -- atr_lowvol_quality_fw

## Run 元信息

| 项 | 值 |
|---|---|
| run_id | 20260805_222941_3bc167 |
| run_started_at | 2026-08-05T22:29:41 |
| runtime_seconds | 420.811 |
| config_hash | d654937ace417d25de3f9c7715ff24bf22fad9e127de1ee5c2679051beeaec3a |
| data_hash | fbce01e7860064d09669bb535ef36a03c3a110cfa15f6e9d0363d5c529092630 |
| universe_hash | a81a7b876e98494b0025afae303441c44e901728530637817e8eba0470207ecf |
| data_source | astock |

## 业绩指标

| 指标 | 值 |
|---|---|
| total_return | 45.01% |
| annual_return | 13.17% |
| max_drawdown | -19.44% |
| sharpe | 0.681 |
| calmar | 0.678 |
| win_rate | 88.38% |
| n_trades | 4797 |

## 持仓概览（期末）

| code | volume | cost | last | pnl |
|---|---|---|---|---|
| 002170.SZ | 300 | 98.37 | 105.39 | 2106.17 |
| 600060.SH | 200 | 169.83 | 182.52 | 2537.18 |
| 600011.SH | 900 | 39.21 | 40.15 | 843.10 |
| 601083.SH | 3100 | 12.16 | 13.14 | 3046.33 |
| 002033.SZ | 700 | 54.38 | 56.20 | 1273.36 |
| 301175.SZ | 6700 | 5.48 | 5.95 | 3148.53 |
| 600351.SH | 600 | 53.88 | 55.47 | 954.11 |
| 001219.SZ | 1300 | 29.11 | 29.86 | 971.32 |
| 603387.SH | 900 | 37.43 | 39.87 | 2202.52 |
| 002562.SZ | 1200 | 30.83 | 31.51 | 818.97 |
| 000655.SZ | 900 | 40.67 | 42.82 | 1937.88 |
| 300573.SZ | 200 | 179.74 | 186.39 | 1328.43 |
| 600267.SH | 400 | 81.47 | 85.02 | 1420.33 |
| 600507.SH | 300 | 109.46 | 119.99 | 3156.88 |
| 002107.SZ | 700 | 54.59 | 52.58 | -1403.57 |
| 000987.SZ | 500 | 69.98 | 69.37 | -308.45 |
| 603345.SH | 300 | 98.38 | 104.69 | 1890.56 |
| 600938.SH | 800 | 37.05 | 42.27 | 4175.32 |
| 600637.SH | 800 | 44.46 | 46.90 | 1946.89 |
| 600558.SH | 1000 | 36.04 | 36.11 | 75.13 |
| 002264.SZ | 1200 | 28.91 | 31.81 | 3488.17 |
| 600598.SH | 1000 | 35.03 | 37.32 | 2285.91 |
| 603279.SH | 1500 | 23.86 | 25.46 | 2412.66 |
| 002039.SZ | 400 | 76.39 | 77.70 | 524.85 |
| 603312.SH | 1100 | 34.20 | 33.72 | -528.04 |
| 000027.SZ | 300 | 118.19 | 122.58 | 1315.57 |
| 600295.SH | 300 | 110.17 | 117.79 | 2286.81 |
| 002159.SZ | 1800 | 20.77 | 21.34 | 1030.81 |
| 002832.SZ | 200 | 123.46 | 156.29 | 6565.76 |
| 600989.SH | 1300 | 25.90 | 28.25 | 3051.10 |
| 000923.SZ | 1300 | 25.06 | 28.48 | 4451.98 |
| 603317.SH | 900 | 39.05 | 42.69 | 3273.24 |
| 001207.SZ | 1600 | 23.48 | 24.37 | 1429.46 |
| 002404.SZ | 1000 | 36.23 | 36.69 | 459.95 |
| 600021.SH | 1000 | 35.17 | 34.97 | -196.19 |
| 601899.SH | 500 | 63.14 | 72.79 | 4822.81 |
| 688208.SH | 800 | 41.47 | 47.57 | 4880.74 |

## 关键日志摘录

```
[ERROR] 2026-07-24 unfilled_order code=000027.SZ reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=600295.SH reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=002159.SZ reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=002832.SZ reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=000923.SZ reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=603317.SH reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=001207.SZ reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=002404.SZ reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=601899.SH reason=below_min_lot
[ERROR] 2026-07-24 unfilled_order code=688208.SH reason=below_min_lot
```

## 数据元信息

- data_path: E:/astock/daily/stock_daily.parquet
- data_mtime: 2026-08-03T00:12:35
- data_adjustment: hfq
- coverage: 2009-01-05 ~ 2026-07-31, 5811 codes
- universe_coverage: 5342/5471 codes have data
- benchmark: enabled
- sector_heat: zero mode

## 复现命令

```bash
python -m scripts.run_backtest --config config/atr_lowvol_fw.yaml
```
