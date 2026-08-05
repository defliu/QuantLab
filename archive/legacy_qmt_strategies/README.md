# 旧项目归档：D:\QMT_STRATEGIES（legacy_qmt_strategies）

> 迁移时间：2026-08-05
> 迁移执行：根据"核心资产迁 QuantLab、一般资产留旧文件夹作瘦归档"的原则。
> 单一事实源：本仓库（QuantLab）已是开发中枢；QMT_STRATEGIES 降级为只读归档。

## 为什么有这个目录

`D:\QMT_STRATEGIES` 是早期 A 股策略研发落地目录，堆积了 9 条并行策略线与大量实验残留。
2026-08 起开发中枢统一迁到 `D:\QuantLab`（即本仓库）。为保留"以前研究过什么 / 踩过什么坑"的史料，
把**有价值且 QuantLab 缺失**的资产迁入本归档，其余一般资产留在 QMT_STRATEGIES 作参考。

## 迁入内容（已校验文件数一致）

| 原路径 | 迁入位置 | 文件数 | 说明 |
|---|---|---|---|
| `config/capital_allocation.yaml` | `QuantLab/config/capital_allocation.yaml` | 32行 | 多策略资金分配**单一事实源**（机器可校验） |
| `scripts/check_capital_allocation.py` | `QuantLab/scripts/check_capital_allocation.py` | 131行 | 资金红线校验器，已在 QuantLab 跑通 EXIT=0 |
| `research/` | `QuantLab/archive/legacy_qmt_strategies/research/` | 238 | 方法论笔记（A股制度/因子库/回测坑位/实盘风控/历史复盘/诚哥偏好） |
| `specs/` | `QuantLab/archive/legacy_qmt_strategies/specs/` | 70 | 设计/评审 SPEC（卖出风控/回测工厂/MIMO/IC/主升浪/MACD/DeepSeek/MCRPS） |
| `deploy/strategy_atr_lowvol*.py` | `QuantLab/projects/Project_ATR_lowvol/build/` | 3 | ATR 低波实盘部署产物（含昨天审计修复的委托代码） |
| `config/atr_lowvol*.yaml` | `QuantLab/config/` | 2 | ATR 低波部署配置 |

> 回测框架 `backtest/`、ATR 策略源 `strategy/atr_lowvol.py`、因子/数据层此前已迁入 QuantLab，本次未重复。

## 留在 QMT_STRATEGIES 作瘦归档（未迁移，请勿误删）

- `agent_hub/`（551 文件）：各 Agent 会话/作战记录，历史归档。
- `Project_*/`：专项工作本。
- `core/` `adapters/` `scripts/` 其余 `deploy/` 其余（6+2 生产/开发版）`atr_lowvol/` `tests/` `data/` `backtest_results/`：Legacy 代码与实验数据，参考用。
- `knowledge_base/`（Obsidian，junction→F: 云盘）：**未动**，长期知识资产。
- `docs/` `specs/` 旧 `global_*.md` 等治理文档：历史参考。

## 删除项（已从 QMT_STRATEGIES 清除）

- `backtest/`（整目录，QuantLab 已有扁平化副本）
- 上述已迁入的副本：`research/` `specs/` `config/capital_allocation.yaml` `scripts/check_capital_allocation.py` `deploy/strategy_atr_lowvol*.py` `config/atr_lowvol*.yaml`
- 垃圾：`D:/` `F:/` 误建子目录、knowledge_base 冲突文件副本、`__pycache__`

## 使用约定

- 新开发只在 QuantLab 进行；QMT_STRATEGIES 只读。
- 如需查"以前的研究/踩坑"，先看 `QuantLab/研究总览与路线图.md`，再按需深入本归档的 `research/` `specs/`。
- Obsidian 知识库（`knowledge_base/`）是另一套长期知识，与本归档互补，不在 QuantLab 内。
