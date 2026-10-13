# DE 任务：P16 项目未完成任务全景盘点

## 任务目标
对 `Project_16_LightGBM股票大师` 做一次**全面、可执行、可交付**的未完成任务盘点，输出结构化清单供人工决策。

## 允许读取的文件清单（仅限这些，禁止读取其他文件）
1. `d:\QuantLab\projects\Project_16_LightGBM股票大师\TODO_PENDING.md` —— 核心待办清单
2. `d:\QuantLab\讨论室\P16三策略优化方案_20261008.md` —— DE 生成的优化方案（含 B1/B2/E-4/E-2/B3/C级）
3. `d:\QuantLab\讨论室\P16三策略止损方向审计_20261009.md` —— DE 生成的止损审计（结论修订为"分池存疑"）
4. `d:\QuantLab\讨论室\P16三策略上线至今收益统计_20261008.md` —— 实盘收益统计（最新至 10-08）
5. `d:\QuantLab\projects\Project_16_LightGBM股票大师\build.py` —— QMT 构建入口
6. `d:\QuantLab\projects\Project_16_LightGBM股票大师\qmt_config.py` —— QMT 配置
6. `d:\QuantLab\projects\Project_16_LightGBM股票大师\strategy\` 目录下核心策略文件（如有）
7. `d:\QuantLab\projects\Project_16_LightGBM股票大师\results\` 目录下最新回测/验证报告

## 禁止事项
- **禁止修改任何源代码/配置/构建产物**
- **禁止 commit / push / 任何 Git 操作**
- **禁止运行训练/回测/重训等耗时脚本**（只做读取、分析、汇总）
- **禁止自行扩大范围去审查无关项目（如 P21/P18 等）**

## 输出要求
输出文件：`d:\QuantLab\projects\Project_16_LightGBM股票大师\results\DE_pending_audit_20261010.md`

输出结构（Markdown）：
1. **执行摘要**（3-5 行：总未完成数、P0/P1/P2 分布、最大风险项）
2. **审计修复路线 R1-R8 逐项现状**（对照 TODO_PENDING.md，注明是否已有代码/脚本/报告支撑）
3. **三大基建待办逐项现状**（补传资产、QMT 内置风控、v4 面板资金流）
3. **优化方案文档中待拍板/待落地项**（B1/B2-②/B4/E-4/E-2/B3/C1-C3 等）
4. **止损审计后续验证路径**（A层观测/B层OOS四组/C层慢门控）
5. **纸面并行实验进度**（v13_paper_parallel 累积配对日、裁决时间窗）
6. **依赖/阻塞关系图**（哪些任务互为前置、哪些卡在用户动作）
7. **建议下一步动作序列**（按 ROI/风险/依赖排序的 Top 5）

## 质量要求
- 每项必须引用**具体文件/行号/表格**作为证据来源
- 发现 TODO_PENDING.md 与讨论室文档不一致处，明确标注"版本差异"
- 发现已完成但未在 TODO 更新的项，标注"TODO 滞后"
- 不编造、不臆测、只汇总现有证据

## 交付时间
单次运行产出上述文件即可，无需多轮迭代。