# DE 验证任务：AMT_RATIO 合格域内排除的真实约束回测

工作日: D:\QuantLab。你是 QuantLab 因子挖掘验证工程师（DE 委托）。
只读既有代码为主，允许新建验证脚本与报告；禁止改动生产代码主路径、禁止 commit、禁止下单。

## 背景
闲时因子挖掘发现 PX_AMT_RATIO_20D（近20日均成交额/流通市值，数学上 ≈ 换手率）可用于排除高活跃股票以提升 ATR 策略表现。已有两个口径：

1. **简化版/Ablation口径 +2.65pp**：在 ATR 合格域 eligible 内部（ATR%<6 + 换手1-8% + 非ST + ROE>0 + 动量12-1>0），按 amt_ratio 剔除"域内相对活跃 top 20%"。见 `research_factor_mining/exclude_ablation_report.md`。
2. **universe层口径 +0.57pp**：在 universe_by_date PIT 快照（全市场剔除 amt_ratio top 20%）后再走 ATR 合格域过滤。见 `research_factor_mining/amt_ablation_engine_report.md` 与 `run_amt_ablation_engine.py`。其增益被削减的主因：universe 层剔除大半与 ATR 已有"换手≤8%"硬过滤重叠。

**目标**：验证"合格域内排除"能否通过**真实约束回测**（backtest/engine：next_open 撮合 / 滑点0.1% / 佣金万2.5 / 印花千0.5 / 涨停拒买 / 跌停拒卖 / 停牌拒单 / 整数手），得到接近简化版 +2.65pp 的真实幅度，从而判断是否可落地为 ATR 实盘参数 `amt_exclude_pct`。

## 允许读取
- D:/QuantLab/strategy/atr_lowvol.py        （ATR 策略源码，已有 max_exclude_pct 域内剔除范式可参考，勿改）
- D:/QuantLab/research_factor_mining/run_amt_ablation_engine.py  （universe层排除版引擎脚本）
- D:/QuantLab/research_factor_mining/exclude_ablation_report.md （简化版/Ablation口径报告）
- D:/QuantLab/research_factor_mining/amt_ablation_engine_report.md（universe层复核报告）
- D:/QuantLab/projects/Project_ATR_lowvol/config/atr_10w_price50.yaml（ATR 载体配置）
- D:/QuantLab/backtest/engine.py 及 backtest/ 相关模块（如需了解引擎接口与 universe_by_date 支持）
- D:/QuantLab/data/astock_reader.py、data/universe.py（如需了解 reader / load_universe）

## 待执行步骤
1. **数据可用性探查**：确认 ATR 策略侧 `market_window[c]` 的 DataFrame 是否含 `amount`（成交额）与 `circ_mv`（流通市值），可算出 amt_ratio = ma20(amount)/circ_mv。若不齐，说明需如何从股源口径补齐（可参照 run_amt_ablation_engine.py 的 build_universe_by_date 从 D:/astock/daily/stock_daily.parquet 取 amount/circ_mv，按日期对齐注入）。
2. **实现（方案二选一，选改动最小且口径最贴合简化版）**：
   - 方案A（策略侧单参数，推荐）：参考 atr_lowvol.py 中 max_exclude_pct 的"域内剔除"范式，新增按 amt_ratio 剔除 eligible 内 top20% 的逻辑。**不要直接改生产 atr_lowvol.py**：复制为新文件 `strategy/atr_lowvol_amt_exclude.py`，注册名 atr_lowvol_amt_exclude，在 eligible 生成后、排序前按 amt_ratio 剔 top20%。运行脚本 import 该新模块即可注册。
   - 方案B（引擎侧 universe_by_date 变形）：改造 run_amt_ablation_engine 思路，让排除点落在 eligible 内部（等价域内剔除）。
   评估后选一个，用真实约束引擎跑「基线 vs 域内排除版」对比。
3. **产出报告**（必须写）：三口径对比表——简化版(+2.65pp) / universe层版(+0.57pp) / 本次域内排除真实约束版；给出 CAGR、最大回撤、夏普、卡玛、交易数、vs基线CAGR（pp）。判定是否显著（建议阈值：CAGR增益 > +0.5pp 且回撤不显著恶化）。
4. **结论**：增益保持 → 建议落地为 ATR 实盘参数 amt_exclude_pct 并进入人工拍板；增益消失/不显著 → 维持研究结论暂不落地。附你的实现选择理由与代码 diff 摘要。

## 必须写的输出文件
- D:/QuantLab/research_factor_mining/de_amt_exclude_engine_report.md（主报告，含三口径对比与结论）
- 方案A 新策略文件（如 D:/QuantLab/strategy/atr_lowvol_amt_exclude.py）或方案B 变体脚本
- 可附运行日志/回测结果 json

## 硬约束
- 禁止 --live、禁止下单/改仓、禁止 commit
- 禁止修改生产 atr_lowvol.py、run_amt_ablation_engine.py、任何 config、backtest/* 及任何既有文件
- 数据缺失/字段不齐如实标注，不得编造数字
- 报告里的所有数字必须以真实跑批为准，注明生成时间与载体口径（atr_10w_price50：10万/8只/季频/ATR%<6/换手1-8%/真实价<50/质量+动量门控/止损-8%）