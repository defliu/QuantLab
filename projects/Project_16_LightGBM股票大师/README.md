# Project_16 · LightGBM 股票大师（阶段0+阶段1）

> 立项时间：2026-08-19
> 目标：在现有量化工作流中引入 LightGBM 模型层，作为"信号增强/辅助排序"工具，
> 与 Project_15 的 F1-F6 评分卡形成双轨选股，并通过"复盘迭代"让模型越训越好。

## 阶段划分

| 阶段 | 文件 | 产出 | 状态 |
|---|---|---|---|
| 阶段0 特征面板 | `build_features.py` | `data/feature_panel.parquet` + `data/features.json` | 可运行 |
| 阶段1 LightGBM 基线 | `train_baseline.py` | `data/baseline_report.json`（IC/ICIR/准确率/分位收益/特征重要性） | 可运行 |
| 阶段2 OPTUNA 寻优+精调 | `train_optuna.py --panel v1` | `D:/QuantLab/models/lgb_model.txt` + `data/optuna_report.json` | 可运行 |
| 阶段4 特征扩展(财务+事件) | `build_features_v2.py` + `train_optuna.py --panel v2` | `data/feature_panel_v2.parquet` + `D:/QuantLab/models/lgb_model_v2.txt` | 可运行 |
| 阶段3 双轨选股部署 | `deploy_predict.py` | `data/selections/YYYYMMDD_selection_dual.md` + CSV（模型×评分卡→Top5） | 可运行 |
| 阶段3b 完整版实时复核 | `review_full.py` + `data/tdx_review.json` | `data/selections/YYYYMMDD_selection_full.md`（TDX 实时 F2/F3/F5 复核） | 可运行 |
| 阶段6 QMT 执行 | `qmt_trader.py` + `qmt_config.py` | miniQMT(xtquant) 按选股结果下单 | dry-run 可跑 |
| 阶段6 盯盘 | `qmt_monitor.py` | xtdata 实时监控 止损/止盈/移动止盈 → 预警 | 需客户端运行 |
| 阶段5 因子IC迭代 | `factor_ic_monitor.py` | `data/factor_ic_report.md/json` + `data/features_v3.json`（剔除11个失效因子） | 可运行 |
| 通宵研究·v3重训 | `train_optuna.py --panel v3` | `feature_panel_v3.parquet` + `D:/QuantLab/models/lgb_model_v3.txt`（27特征） | 已完成 |
| 通宵研究·双轨回测 | `backtest_dual.py` | `data/backtest_dual_report.json`（胜率54%/盈亏比1.38/日均超额0.53%） | 已完成 |
| 通宵研究·滚动评估 | `rolling_eval.py` | `data/rolling_eval_report.json`（季度IC稳定性） | 已完成 |

## 数据管线

- 日线行情：`E:/astock/daily/stock_daily.parquet`（索引 `(trade_date, ts_code)`，后复权前原始价，含丰富字段）
- 股票池：`D:/QuantLab/data/universe_all_a.csv`（`code/enabled`）
- 剔除：未启用 / ST / 科创板(688) / 北交所(4,8开头)
- 标签：T日收盘用 T 及之前信息预测未来 `--fwd`（默认1）日收益是否为正（二分类），同时保存连续收益用于 IC

## 运行

```bash
# 阶段0：全量特征面板（warmup 自 2018-01-01，面板自 2019-01-01）
python build_features.py
# 调试用（小规模快速验证）：
python build_features.py --limit 300 --start 2022-01-01 --panel_start 2023-01-01

# 阶段1：LightGBM 基线 + IC 评估（默认 train/valid/test 时序切分）
python train_baseline.py
# 自定义切分：
python train_baseline.py --split-train 2020-01-01/2023-06-30 --split-valid 2023-07-01/2024-06-30 --split-test 2024-07-01/2026-08-14

# 阶段2：OPTUNA 寻优（目标=验证集IC）+ 精调 + 保存模型
python train_optuna.py --n-trials 20
# 附加季度滚动重训评估（可选，较耗时）：
python train_optuna.py --n-trials 20 --rolling --rolling-folds 6
# 快速验证：
python train_optuna.py --n-trials 3 --limit-rows 300000 --split-train 2020-01-01/2024-12-31 --split-valid 2025-01-01/2025-12-31 --split-test 2026-01-01/2026-08-14
```

# 阶段3：双轨选股（模型候选）
python deploy_predict.py --top-k 5

# 阶段3b：完整版实时复核（TDX）
# 1) AI 通过 TDX MCP 采集候选股资金流/新闻/行业涨幅，写入 data/tdx_review.json
# 2) 一条命令生成完整版清单：
python review_full.py --candidates data/selections/20260814_model_top5.csv
```

## 关键纪律（来自 LightGBM 知乎资料整理）

1. **因子 > 模型**：特征工程质量决定 95% 效果，先跑基线看 IC，不显著别调参。
2. **禁止随机 K 折**：必须用 walk-forward 时序切分（本脚本已内置）。
3. **禁止前视偏差**：特征只用 T 日及之前；注意未来成分股/财报泄露/涨停买不到（聚宽教训）。
4. **强正则化**：量化信噪比极低，`lambda_l2` 可给大值；`num_leaves≤31`、`max_depth≤6`、`min_child_samples` 用大值。
5. **IC 是核心指标**：评估用 IC/ICIR，而非纯准确率（55% 准确率是常态，ML 是辅助排序工具）。
6. **基线红线**：test IC 不为正说明特征/标签有问题，先修，再进阶段2。

## 依赖

`pip install lightgbm pyarrow scikit-learn scipy`

## 目录

```
Project_16_LightGBM股票大师/
├── build_features.py      # 阶段0
├── train_baseline.py      # 阶段1
├── README.md
└── data/                  # 产出（面板+报告）
```

---
*仅供个人量化研究使用，不构成投资建议，市场有风险。*
