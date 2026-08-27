# 通宵任务说明（2026-08-24 夜）

> 目的：用 a-stock-data skill 拉真实数据，验证能否改善可执行口径超额，产出满意数据结果。
> **红线：绝不触碰 V1.1 资产**（明天 8/25 开盘上线）。

## V1.1 资产（只读，禁止修改/覆盖）
- `D:/QuantLab/models/lgb_model_v3.txt`（当前复权版 1437 树，明天 deploy 用）
- `deploy_predict.py` / `qmt_config.py` / `data_config.py`（明天盘后链路用）
- `run_scheduled.ps1`（已修复 Python 选择 bug，勿再改）
- 所有产物一律用新文件名（`*_v3.1` / `*_real` 后缀），写入 `data/real/` 或独立目录

## 已验证的数据能力（a-stock-data skill v3.7.1）
- ✅ **新浪资金流备胎 `fund_flow_backup`**：个股日级主力净流入，到 2026-08-21（5 只测试成功，各 60 条）
- ✅ **东财分钟级资金流 `eastmoney_fund_flow_minute`**（测试通过，100 条）
- ✅ **东财个股新闻 `eastmoney_stock_news`**（恒邦中报+112.6% 与 8/21 清单吻合）
- ✅ **板块归属 `eastmoney_concept_blocks`**（002237 → 19 个板块）
- ⚠️ **东财板块资金流/行业排名 `board_fund_flow`/`industry_comparison`**：当前代理下被拦截（ProxyError，间歇性），需重试或换时段
- ⚠️ 东财 push2his 日级资金流：代理拦截，用新浪备胎替代

## 数据源位置
- SKILL.md：`c:\Users\Administrator\.trae-cn\work\6a856dd08ac25249ed9d6c30\a-stock-data-skill\SKILL.md`
- AST 加载方式见 `test_astockdata3.py`（只加载函数定义，跳过示例调用）
- 已抓取样本：`data/real/fund_flow_sample.parquet`（5只×60天，新浪资金流）

## 建议执行路径（网络允许时）
1. **F2 真实资金流**：批量拉回测期关键个股 `fund_flow_backup`（新浪，到 8/21），构建 `main_net` 特征
2. **F5 板块真实信号**：重试东财 `board_fund_flow`/`industry_comparison`（隔几分钟重试），或先用本地行业动量（已验证）
3. **面板增强 v3.1**：在 v3 基础上加 F2 资金流 + F5 板块信号 → `feature_panel_v3.1.parquet`
4. **重训**：`train_optuna.py --panel-file feature_panel_v3.1.parquet --model-tag _v3.1`
5. **可执行回测**：`scan_rotate_cost.py`（环境变量 BT_* 指向新面板/模型，输出到新报告文件）
6. **对比**：v3.1 vs v3_enh（+0.056%）vs v3 基线，产出结论

## 结果落盘
- 结论写入 `data/real/OVERNIGHT_RESULT.md`，不进正式报告，待用户审阅
