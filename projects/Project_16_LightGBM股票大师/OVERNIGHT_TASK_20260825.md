# 通宵任务说明书 2026-08-25

> 执行方式：`python overnight_20260825.py`（自动运行，约 1-2 小时）
> 进度：`data/real/OVERNIGHT_20260825_PROGRESS.log`
> 结论：`data/real/OVERNIGHT_20260825_RESULT.md`
> 严守隔离：只产研究面板/候选模型，不触碰 V1.1 生产资产（正式模型已只读保护）。

## 背景
采购数据已整合到 `E:/astock`（详见 `E:/astock/DATA_MANIFEST.md`）：
- `moneyflow/` 个股资金流**五档**（2007 起）→ F2 真实化
- `board_fundflow/` 行业+概念板块资金流（东财 2023-09 起）→ F5 真实化
- `lhb/` 龙虎榜/涨跌停、`auction/` 集合竞价、`research/` 研报、`chip/` 筹码分布、`northbound/` 北向、`index_weight/` 指数权重

## 任务一：复核「数据不全时做过的 F2/F5 真实化」（之前受限于无五档/板块数据）
1. 构建真实 F2（moneyflow 五档：主力净额/超大单净额/净占比）与真实 F5（东财行业资金流 + 板块涨幅）→ `data/feature_panel_v3_sc_real.parquet`
2. IC 对比：真实 vs 代理（新浪 main_net、代理行业涨幅）
3. 重训 v3 候选（8/19 参数 + 真实 F2/F5 特征）→ 候选模型 + test IC
4. 评分卡真实回测（`scan_rotate_cost_real.py --exec`，真实 F2/F5 面板，模型=v3_enh）→ `scan_rotate_cost_real_v2_report.md`，对比 v1（新浪口径）基线

## 任务二：衍生因子 IC 研究（新数据方向）
| 方向 | 数据 | 因子 |
|---|---|---|
| 筹码分布 | `chip/cyq_daily.parquet` | 获利盘比例、成本中位、成本分布宽度 |
| 龙虎榜 | `lhb/top_list.parquet` | 净买额、上榜次数 |
| 集合竞价 | `auction/stock_auction_o_daily.parquet` | 竞价涨幅、竞价量比 |
| 研报一致预期 | `research/report_rc_daily.parquet` | 研报数量、评级均值 |
| 北向资金 | `northbound/hk_hold_full.parquet` | 持股比例、持股变动 |
| 板块轮动 | `board_fundflow` | 行业资金流、板块涨幅 |

## 判断标准
- IC 对比：真实 F2/F5 是否优于代理（IC 提升、ICIR 提升）
- 候选模型 test IC 是否 ≥ v3_enh（≈0.044）
- 评分卡真实回测可执行口径是否改善
- 新因子 IC| 显著（|ICIR|≥0.15 为强）的纳入下一步因子库

## 注意事项
- 不修改任何 V1.1 生产文件；模型候选存 `versions/models/`、面板存 `data/`
- moneyflow 单位为万元；cyq 日期为 int64 需转换；行业资金流 2023-09 起（回测期全覆盖）
