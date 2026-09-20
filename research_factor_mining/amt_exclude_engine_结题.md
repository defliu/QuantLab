# DE 验证结题：AMT_RATIO 合格域内排除 —— 真实约束回测 +0.00pp，不落地

- 生成: 2026-09-17 | 任务: research_factor_mining/de_amt_exclude_task.md（方案A：策略侧单参数域内剔除）
- DE 执行，opencode 以原始证据独立复核后落盘
- 判定：**CAGR 增益 +0.00pp，未达 >+0.5pp 阈值 → 不落地为 ATR 实盘参数 `amt_exclude_pct`**

## 1. 三种口径对比（对齐 | 结论可信度）

| 口径 | 载体 | 候选池 | 选区语义 | CAGR 差 |
|---|---|---|---|---|
| 简化版 Ablation | exclude_ablation_report.md | 小盘价值池（circ_mv<30亿+PE>0+PB>0） | 因子 `sub.quantile(0.8)` 剔 top20% | **+2.65pp**（有期数伪差异，见 §3） |
| universe 层复核 | amt_ablation_engine_report.md | 全市场 | 剔 amt_ratio top20% 后再走 ATR | **+0.57pp** |
| **DE 域内排除（本轮）** | amt_exclude_engine_results.json | 生产 ATR 完善池 | ATE eligible 内部再剔 top20% | **+0.00pp** |

DE 本轮用真实约束回测引擎（backtest/engine：next_open 撮合 / 滑点 0.1% / 佣金万2.5 / 印花千0.5 / 涨停拒买 / 跌停拒卖 / 停牌拒单 / 整数手），基线（atr_lowvol）vs 排除版（atr_lowvol_amt_exclude, amt_exclude_pct=0.20），同 reader/同窗口/同配置，区间 2019-01-01~2026-09-11。

**base 与 excl 逐位一致**：cagr=0.1691 / mdd=-0.2480 / sharpe=0.898 / calmar=0.682 / trades=666 / total_return=2.1845。`amt_excl_by_rebalance=[]`。

## 2. 证据链：排除逻辑真实运行，但与被选区几乎不相交

初看 base/excl 逐位一致 + `amt_excl_by_rebalance=[]` 看似"假跑"，实为**真实验结果**，三个独立证据均可验证：

1. **诊断计数**：excl 报告 `diagnostics_aggregate.strategy_specific.amt_exclude.excluded_avg_per_day=2.90`、`eligible_left_avg_per_day=11.57`，证明每次调仓确实剔除了净值高的活跃股、且剔后剩池 11.57>8 仍有富余；base 无此键。
2. **重叠诊断**（diag_amt_overlap_results.json）：逐季复现 eligible，31 个季度被选 ATR%最低 8 只与 amt_ratio top20% 剔除集 **overlap≈0**（仅 2020-04 / 2022-07 小盘池各 1 只次），生产池全季度 overlap=0。
3. **分位位置**：被选 8 只的 amt_ratio 分位中位数普遍 <0.35、多数 <0.2，即 ATR 低波偏好选的票天然是低活跃股，根本不落在高活跃剔除集。

**根因**：ATR 策略按 ATR% 升序选前 8，且已有换手 ≤8% 硬过滤。amt_ratio=ma20(amount)/circ_mv 数学上≈换手率，top20% 平均换手约 6%，而 ATR 低波偏好保留平均换手约 2.33%——二者天然正交，域内再排除冗余无效。

## 3. 简化版 +2.65pp 的期数折算伪差异

简化版 PX_AMT_RATIO_20D 排除后为 19 期（基线 20 期），其 CAGR 用 `nav^(4/len)` 折算：同样的净值路径用更小的期数做指数折算，被折算出虚高的 CAGR（报告 37.01%）。剔除期数差后，真实换股增量效应仅约 +0.4pp 量级（universe 层 +0.57pp 是排除该伪差后的干净口径）。且简化版用小盘价值池而非生产池，非生产口径。

## 4. 结论

- **不落地**：真实约束下 AMT_RATIO 域内排除对 ATR 生产策略 **+0.00pp** 无效，未达判定阈值。
- 简化版 +2.65pp 为期数折算伪差 + 非生产池叠加的结果，不代表真实可落地增益。
- universe 层剔除（+0.57pp）虽轻微为正，但改动面大（全池预处理），且与 ATR 自身换手过滤高度冗余，不具实际落地价值，维持不纳入。

## 5. 产物清单

- 任务书：research_factor_mining/de_amt_exclude_task.md
- 回测脚本：research_factor_mining/run_amt_exclude_engine.py
- 结果：research_factor_mining/amt_exclude_engine_results.json
- 重叠诊断脚本/结果：research_factor_mining/diag_amt_overlap.py / diag_amt_overlap_results.json
- 验证策略版：strategy/atr_lowvol_amt_exclude.py（`@register_strategy("atr_lowvol_amt_exclude")`，**研究用勿实盘**，与生产 atr_lowvol.py 隔离，amt_exclude_pct 默认 0 时行为与生产完全一致）